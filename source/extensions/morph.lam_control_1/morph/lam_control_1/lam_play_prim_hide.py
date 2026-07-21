"""CSV Play · 「prim숨김」 prim visibility (설정: lam_viewport_overlay_config).

Fade 는 **update 이벤트(프레임)** 마다 opacity 를 갱신한다.
(main 스레드 sleep 만 쓰면 뷰포트가 안 그려져 duration 후 한 번에 사라짐)

Play 시작 fade: 이미 숨겨진 prim 은 다시 보이지 않음 — **현재 보이는 것만** fade hide.
"""

from __future__ import annotations

import contextvars
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Literal, Optional, Tuple

import omni.usd as ou  # type: ignore  # noqa: E402
from pxr import Sdf, Usd, UsdGeom, UsdShade  # type: ignore  # noqa: E402

_PRINT_PREFIX = "[LAM/PlayPrimHide]"

PlayHidePhase = Literal["play_start", "play_stop_reset", "ui_hide", "ui_show"]

_lock = threading.Lock()
# context_key("") = 화면1 기본 USD context / 그 외 = aux context 이름
_visibility_snapshot_by_ctx: Dict[str, Dict[str, str]] = {}

_UI_PHASES = frozenset({"ui_hide", "ui_show"})
_ui_phase_epoch_by_ctx: Dict[str, int] = {}
_usd_context_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "lam_prim_hide_usd_ctx",
    default="",
)
_hide_checked_var: contextvars.ContextVar[Optional[bool]] = contextvars.ContextVar(
    "lam_prim_hide_checked",
    default=None,
)


_OPACITY_INPUT_CANDIDATES: Tuple[str, ...] = (
    "opacity_constant",
    "opacity",
    "opacityAmount",
)
_ENABLE_OPACITY_INPUT = "enable_opacity"


@dataclass
class _ShaderOpacitySlot:
    shader_path: str
    input_name: str = "opacity_constant"
    had_authored: bool = False
    original: Optional[float] = None
    enable_opacity_path: Optional[str] = None
    enable_was_authored: bool = False
    enable_original: Optional[bool] = None


@dataclass
class _FadeTargets:
    root_path: str
    gprim_paths: List[str] = field(default_factory=list)
    sorted_gprim_paths: List[str] = field(default_factory=list)
    shader_slots: List[_ShaderOpacitySlot] = field(default_factory=list)
    # mdl: RTX MDL opacity_constant | progressive: Gprim 순차 hide (RTX CAD fallback)
    fade_mode: str = "progressive"


def apply_play_prim_hide_phase(phase: PlayHidePhase) -> bool:
    phase_s = str(phase)
    try:
        # ui_hide/ui_show: fire-and-forget (main update 콜백 안에서 wait 하면 deadlock)
        if phase_s in _UI_PHASES:
            _schedule_instant_on_main(phase_s)
            return True
        if phase_s == "play_start" and _any_spec_fade_for_hide():
            return _apply_play_start_fade_async()
        return _run_instant_on_main_wait(phase_s)
    except Exception as exc:
        print(f"{_PRINT_PREFIX} phase={phase_s} failed: {exc}", flush=True)
        return False


def _bump_ui_phase_epoch(ctx: Optional[str] = None) -> int:
    key = str(ctx if ctx is not None else _current_context_key() or "").strip()
    with _lock:
        n = int(_ui_phase_epoch_by_ctx.get(key, 0)) + 1
        _ui_phase_epoch_by_ctx[key] = n
        return n


def _ui_phase_epoch_for(ctx: Optional[str] = None) -> int:
    key = str(ctx if ctx is not None else _current_context_key() or "").strip()
    with _lock:
        return int(_ui_phase_epoch_by_ctx.get(key, 0))


def apply_play_prim_hide_ui_instant(phase: PlayHidePhase) -> bool:
    """post_update·UI 콜백 등 **이미 main thread** 일 때 즉시 적용 (wait 없음)."""
    phase_s = str(phase)
    if phase_s not in _UI_PHASES:
        return apply_play_prim_hide_phase(phase)
    try:
        _bump_ui_phase_epoch(_current_context_key())
        _apply_phase_instant(phase_s)
        return True
    except Exception as exc:
        print(f"{_PRINT_PREFIX} ui_instant phase={phase_s} failed: {exc}", flush=True)
        return False


@contextmanager
def play_prim_hide_stage_context(context_name: Optional[str]):
    """지정 USD context Stage 에 prim 숨김 적용 (화면2 분할 stage)."""
    key = str(context_name or "").strip()
    token = _usd_context_var.set(key)
    try:
        yield
    finally:
        _usd_context_var.reset(token)


def _current_context_key() -> str:
    return str(_usd_context_var.get() or "").strip()


def _snap_store() -> Dict[str, str]:
    key = _current_context_key()
    with _lock:
        store = _visibility_snapshot_by_ctx.get(key)
        if store is None:
            store = {}
            _visibility_snapshot_by_ctx[key] = store
        return store


def apply_play_prim_hide_ui_instant_for_context(
    context_name: str,
    phase: PlayHidePhase,
    *,
    prim_hide_checked: Optional[bool] = None,
) -> bool:
    checked_token = None
    if prim_hide_checked is not None:
        checked_token = _hide_checked_var.set(bool(prim_hide_checked))
    try:
        with play_prim_hide_stage_context(context_name):
            return apply_play_prim_hide_ui_instant(phase)
    finally:
        if checked_token is not None:
            _hide_checked_var.reset(checked_token)


def apply_play_prim_hide_phase_for_context(
    context_name: str,
    phase: PlayHidePhase,
    *,
    prim_hide_checked: Optional[bool] = None,
) -> bool:
    checked_token = None
    if prim_hide_checked is not None:
        checked_token = _hide_checked_var.set(bool(prim_hide_checked))
    try:
        with play_prim_hide_stage_context(context_name):
            return apply_play_prim_hide_phase(phase)
    finally:
        if checked_token is not None:
            _hide_checked_var.reset(checked_token)


def _smoothstep01(t: float) -> float:
    t = max(0.0, min(1.0, float(t)))
    return t * t * (3.0 - 2.0 * t)


def _schedule_instant_on_main(phase: str) -> None:
    ctx = _current_context_key()
    epoch = _ui_phase_epoch_for(ctx)
    checked = _hide_checked_var.get()

    def _run() -> None:
        if epoch != _ui_phase_epoch_for(ctx):
            return
        checked_token = None
        if checked is not None:
            checked_token = _hide_checked_var.set(bool(checked))
        try:
            with play_prim_hide_stage_context(ctx or None):
                _apply_phase_instant(phase)
        finally:
            if checked_token is not None:
                _hide_checked_var.reset(checked_token)

    try:
        from .lam_sequence_engine import _dispatch_main

        _dispatch_main(_run)
    except Exception:
        _run()


def _run_instant_on_main_wait(phase: str) -> bool:
    err: List[Optional[BaseException]] = [None]
    ctx = _current_context_key()
    checked = _hide_checked_var.get()

    def _run() -> None:
        checked_token = None
        if checked is not None:
            checked_token = _hide_checked_var.set(bool(checked))
        try:
            with play_prim_hide_stage_context(ctx or None):
                _apply_phase_instant(phase)
        except BaseException as e:
            err[0] = e
            raise
        finally:
            if checked_token is not None:
                _hide_checked_var.reset(checked_token)

    try:
        from .lam_sequence_engine import _dispatch_main_wait

        ok = _dispatch_main_wait(_run, timeout=8.0)
    except Exception:
        with play_prim_hide_stage_context(ctx or None):
            _apply_phase_instant(phase)
        ok = True
    if err[0] is not None:
        print(f"{_PRINT_PREFIX} phase={phase} failed: {err[0]}", flush=True)
    return bool(ok)


def planned_play_prim_hide_duration_sec() -> float:
    """play_start 숨김 예상 소요 — fade 합산, 없으면 즉시(≈0)."""
    specs = _load_specs()
    if not specs:
        return 0.0
    if _any_spec_fade_for_hide():
        return sum(
            _resolve_fade_duration(s)
            for s in specs
            if _resolve_fade_enabled(s) and _resolve_fade_hide_in(s)
        )
    return 0.0


def play_prim_hide_specs_configured() -> bool:
    """PLAY_HIDE / PLAY_SHOW spec 이 하나라도 설정됐는지."""
    return bool(_load_specs() or _load_show_specs())


def kickoff_play_prim_hide_play_start(
    done: threading.Event,
    *,
    usd_context_name: str = "",
    on_hide_complete: Optional[Callable[[], None]] = None,
) -> bool:
    """play_start 숨김 — main 에 시작만 걸고 worker 는 ``done`` 으로 완료 대기."""
    specs = _load_specs()
    show_specs = _load_show_specs()
    if not specs and not show_specs:
        done.set()
        return False

    ctx = str(usd_context_name or "").strip()
    err: List[Optional[BaseException]] = [None]
    sync_checkbox = bool(specs)

    def _finish_hide() -> None:
        if sync_checkbox and callable(on_hide_complete):
            try:
                on_hide_complete()
            except Exception as exc:
                print(
                    f"{_PRINT_PREFIX} play_start checkbox sync failed: {exc}",
                    flush=True,
                )
        done.set()

    def _kickoff() -> None:
        try:
            with play_prim_hide_stage_context(ctx or None):
                if specs and _any_spec_fade_for_hide():
                    _start_play_hide_fade_chain(
                        _finish_hide,
                        usd_context_name=ctx,
                    )
                else:
                    _apply_phase_instant("play_start")
                    _finish_hide()
        except BaseException as e:
            err[0] = e
            done.set()

    try:
        from .lam_sequence_engine import _dispatch_main_wait

        if not _dispatch_main_wait(_kickoff, timeout=5.0):
            print(f"{_PRINT_PREFIX} play_start kickoff timeout", flush=True)
            done.set()
            return False
    except Exception as exc:
        print(f"{_PRINT_PREFIX} play_start kickoff failed: {exc}", flush=True)
        try:
            _kickoff()
        except Exception:
            done.set()
        return False
    if err[0] is not None:
        print(f"{_PRINT_PREFIX} play_start failed: {err[0]}", flush=True)
    return True


def _apply_play_start_fade_async() -> bool:
    """fade: main 에서 update 구독 시작 → 호출 스레드는 완료까지 wait (렌더는 계속)."""
    ctx = _current_context_key()
    total = sum(
        _resolve_fade_duration(s)
        for s in _load_specs()
        if _resolve_fade_enabled(s) and _resolve_fade_hide_in(s)
    )
    done = threading.Event()
    err: List[Optional[BaseException]] = [None]

    def _kickoff() -> None:
        try:
            with play_prim_hide_stage_context(ctx or None):
                _start_play_hide_fade_chain(lambda: done.set(), usd_context_name=ctx)
        except BaseException as e:
            err[0] = e
            done.set()

    try:
        from .lam_sequence_engine import _dispatch_main_wait

        if not _dispatch_main_wait(_kickoff, timeout=5.0):
            print(f"{_PRINT_PREFIX} play_start fade kickoff timeout", flush=True)
            return False
    except Exception as exc:
        print(f"{_PRINT_PREFIX} play_start fade kickoff failed: {exc}", flush=True)
        with play_prim_hide_stage_context(ctx or None):
            _start_play_hide_fade_chain(lambda: done.set(), usd_context_name=ctx)

    ok = done.wait(timeout=max(15.0, total + 8.0))
    if err[0] is not None:
        print(f"{_PRINT_PREFIX} play_start fade failed: {err[0]}", flush=True)
    if not ok:
        print(f"{_PRINT_PREFIX} play_start fade wait timeout", flush=True)
    return bool(ok and err[0] is None)


def _load_specs():
    try:
        from .lam_viewport_overlay_config import PLAY_HIDE_PRIM_SPECS

        return list(PLAY_HIDE_PRIM_SPECS or [])
    except Exception:
        return []


def _load_show_specs():
    try:
        from .lam_viewport_overlay_config import PLAY_SHOW_PRIM_SPECS

        return list(PLAY_SHOW_PRIM_SPECS or [])
    except Exception:
        return []


def prim_hide_specs_stage_status() -> Tuple[int, int]:
    """``PLAY_HIDE_PRIM_SPECS`` 중 stage 에서 Imageable 로 찾은 개수 / 전체."""
    specs = _load_specs()
    paths = [
        str(getattr(s, "prim_path", "") or "").strip()
        for s in specs
        if str(getattr(s, "prim_path", "") or "").strip()
    ]
    total = len(paths)
    if total <= 0:
        return 0, 0
    found = 0
    for path in paths:
        _, img = _get_imageable(path)
        if img is not None:
            found += 1
    return found, total


def _resolve_fade_enabled(spec) -> bool:
    from .lam_viewport_overlay_config import PLAY_HIDE_FADE_ENABLED

    if getattr(spec, "fade_enabled", None) is not None:
        return bool(spec.fade_enabled)
    return bool(PLAY_HIDE_FADE_ENABLED)


def _resolve_fade_duration(spec) -> float:
    from .lam_viewport_overlay_config import PLAY_HIDE_FADE_DURATION_SEC

    v = getattr(spec, "fade_duration_sec", None)
    if v is not None:
        return max(0.05, float(v))
    return max(0.05, float(PLAY_HIDE_FADE_DURATION_SEC))


def _resolve_fade_hide_in(spec) -> bool:
    from .lam_viewport_overlay_config import PLAY_HIDE_FADE_HIDE_IN

    v = getattr(spec, "fade_hide_in", None)
    return bool(PLAY_HIDE_FADE_HIDE_IN if v is None else v)


def _any_spec_fade_for_hide() -> bool:
    return any(
        _resolve_fade_enabled(s) and _resolve_fade_hide_in(s) for s in _load_specs()
    )


def _get_stage():
    cn = _current_context_key()
    if cn:
        try:
            ctx = ou.get_context(str(cn))
            return ctx.get_stage() if ctx else None
        except Exception:
            return None
    try:
        ctx = ou.get_context()
        return ctx.get_stage() if ctx else None
    except Exception:
        return None


def _session_edit(stage):
    layer = stage.GetSessionLayer()
    return Usd.EditContext(stage, layer)


def _get_imageable(path: str):
    stage = _get_stage()
    if stage is None:
        return None, None
    prim = stage.GetPrimAtPath(path)
    if not prim or not prim.IsValid():
        return None, None
    img = UsdGeom.Imageable(prim)
    if not img:
        return None, None
    return prim, img


def _collect_gprim_paths(root_path: str) -> List[str]:
    stage = _get_stage()
    if stage is None:
        return []
    root = stage.GetPrimAtPath(root_path)
    if not root or not root.IsValid():
        return []
    out: List[str] = []
    seen: set[str] = set()
    try:
        for prim in Usd.PrimRange(root, Usd.TraverseInstanceProxies()):
            if not prim.IsA(UsdGeom.Gprim):
                continue
            ps = prim.GetPath().pathString
            if ps not in seen:
                seen.add(ps)
                out.append(ps)
    except Exception:
        if root.IsA(UsdGeom.Gprim):
            out.append(root_path)
    return out


def _shaders_for_material(mat: UsdShade.Material) -> List[Usd.Prim]:
    out: List[Usd.Prim] = []
    try:
        for prim in Usd.PrimRange(mat.GetPrim()):
            if prim.IsA(UsdShade.Shader):
                out.append(prim)
    except Exception:
        sp = mat.ComputeSurfaceSource()
        if sp and sp[0]:
            out.append(sp[0])
    return out


def _pick_opacity_input(shader: UsdShade.Shader) -> Optional[str]:
    for name in _OPACITY_INPUT_CANDIDATES:
        inp = shader.GetInput(name)
        if inp:
            return name
    return None


def _slot_from_shader(shader_prim: Usd.Prim) -> Optional[_ShaderOpacitySlot]:
    if not shader_prim or not shader_prim.IsValid():
        return None
    shader = UsdShade.Shader(shader_prim)
    name = _pick_opacity_input(shader)
    if not name:
        return None
    sp = shader_prim.GetPath().pathString
    inp = shader.GetInput(name)
    had = bool(inp and inp.HasAuthoredValue())
    orig = None
    try:
        if had and inp is not None:
            orig = float(inp.Get())
    except Exception:
        orig = None
    enable_path: Optional[str] = None
    enable_was = False
    enable_orig: Optional[bool] = None
    en_inp = shader.GetInput(_ENABLE_OPACITY_INPUT)
    if en_inp:
        enable_path = _ENABLE_OPACITY_INPUT
        enable_was = bool(en_inp.HasAuthoredValue())
        try:
            if enable_was:
                enable_orig = bool(en_inp.Get())
        except Exception:
            enable_orig = None
    return _ShaderOpacitySlot(
        shader_path=sp,
        input_name=name,
        had_authored=had,
        original=orig,
        enable_opacity_path=enable_path,
        enable_was_authored=enable_was,
        enable_original=enable_orig,
    )


def _collect_shader_slots(gprim_paths: List[str]) -> List[_ShaderOpacitySlot]:
    stage = _get_stage()
    if stage is None:
        return []
    slots: List[_ShaderOpacitySlot] = []
    seen: set[str] = set()
    for gp in gprim_paths:
        prim = stage.GetPrimAtPath(gp)
        if not prim or not prim.IsValid():
            continue
        try:
            binding = UsdShade.MaterialBindingAPI(prim)
            mat, _ = binding.ComputeBoundMaterial()
            if not mat:
                continue
            for shader_prim in _shaders_for_material(mat):
                sp = shader_prim.GetPath().pathString
                if sp in seen:
                    continue
                slot = _slot_from_shader(shader_prim)
                if slot is None:
                    continue
                seen.add(sp)
                slots.append(slot)
        except Exception:
            continue
    return slots


def _preload_mdl_shader_inputs(slots: List[_ShaderOpacitySlot]) -> None:
    """MDL inputs 는 lazy — fade 전에 shader prim 파라미터를 stage 에 올린다."""
    if not slots:
        return
    try:
        cn = _current_context_key()
        ctx = ou.get_context(str(cn)) if cn else ou.get_context()
        if ctx is None:
            return
        load_fn = getattr(ctx, "load_mdl_parameters_for_prim", None)
        for slot in slots:
            if not callable(load_fn):
                break
            try:
                load_fn(slot.shader_path)
            except Exception:
                pass
    except Exception:
        pass


def _build_fade_targets(
    root_path: str,
    *,
    visible_gprims_only: bool = False,
) -> _FadeTargets:
    gpaths = _collect_gprim_paths(root_path)
    if visible_gprims_only:
        gpaths = [p for p in gpaths if _prim_is_draw_visible(p)]
    slots = _collect_shader_slots(gpaths)
    _preload_mdl_shader_inputs(slots)
    if not slots:
        slots = _collect_shader_slots(gpaths)
    mode = "mdl" if slots else ("progressive" if gpaths else "instant")
    return _FadeTargets(
        root_path=root_path,
        gprim_paths=gpaths,
        sorted_gprim_paths=sorted(gpaths),
        shader_slots=slots,
        fade_mode=mode,
    )


def _visibility_token(img: UsdGeom.Imageable) -> str:
    try:
        attr = img.GetVisibilityAttr()
        if attr and attr.HasAuthoredValueOpinion():
            v = attr.Get()
            if v == UsdGeom.Tokens.invisible:
                return "invisible"
    except Exception:
        pass
    return "inherited"


def _prim_is_draw_visible(path: str) -> bool:
    """현재 뷰포트에서 그려지는지(상속 포함)."""
    _, img = _get_imageable(path)
    if img is None:
        return False
    try:
        vis = img.ComputeVisibility(Usd.TimeCode.Default())
        return vis != UsdGeom.Tokens.invisible
    except Exception:
        return _visibility_token(img) != "invisible"


def _read_mdl_opacity(targets: _FadeTargets) -> float:
    """fade 시작 전 MDL opacity (없으면 1.0)."""
    stage = _get_stage()
    if stage is None or not targets.shader_slots:
        return 1.0
    vals: List[float] = []
    for slot in targets.shader_slots:
        prim = stage.GetPrimAtPath(slot.shader_path)
        if not prim or not prim.IsValid():
            continue
        try:
            inp = UsdShade.Shader(prim).GetInput(slot.input_name)
            if inp and inp.HasAuthoredValue():
                vals.append(float(inp.Get()))
        except Exception:
            pass
    if not vals:
        return 1.0
    return float(max(0.0, min(1.0, max(vals))))


def _capture_snapshot(path: str) -> None:
    store = _snap_store()
    with _lock:
        if path in store:
            return
    _, img = _get_imageable(path)
    if img is None:
        return
    tok = _visibility_token(img)
    with _lock:
        if path not in store:
            store[path] = tok


def _apply_visibility_token(path: str, token: str) -> None:
    _, img = _get_imageable(path)
    if img is None:
        return
    try:
        if token == "invisible":
            img.MakeInvisible()
        else:
            img.GetVisibilityAttr().Set(UsdGeom.Tokens.inherited)
            img.MakeVisible()
    except Exception as exc:
        print(f"{_PRINT_PREFIX} restore({path}) failed: {exc}", flush=True)


def _set_visible_immediate(path: str, visible: bool) -> None:
    _, img = _get_imageable(path)
    if img is None:
        return
    try:
        if visible:
            img.MakeVisible()
        else:
            img.MakeInvisible()
    except Exception as exc:
        print(
            f"{_PRINT_PREFIX} set_visible({path},{visible}) failed: {exc}",
            flush=True,
        )


def _apply_mdl_fade_opacity(targets: _FadeTargets, opacity: float) -> None:
    stage = _get_stage()
    if stage is None or not targets.shader_slots:
        return
    op = float(max(0.0, min(1.0, opacity)))
    try:
        with _session_edit(stage):
            for slot in targets.shader_slots:
                prim = stage.GetPrimAtPath(slot.shader_path)
                if not prim or not prim.IsValid():
                    continue
                shader = UsdShade.Shader(prim)
                if slot.enable_opacity_path:
                    en = shader.GetInput(slot.enable_opacity_path)
                    if not en:
                        en = shader.CreateInput(
                            slot.enable_opacity_path, Sdf.ValueTypeNames.Bool
                        )
                    if en:
                        try:
                            en.Set(True)
                        except Exception:
                            pass
                inp = shader.GetInput(slot.input_name)
                if not inp:
                    inp = shader.CreateInput(
                        slot.input_name, Sdf.ValueTypeNames.Float
                    )
                if inp:
                    try:
                        inp.Set(op)
                    except Exception:
                        pass
    except Exception as exc:
        print(f"{_PRINT_PREFIX} apply_mdl_fade_opacity failed: {exc}", flush=True)


def _apply_progressive_hide(targets: _FadeTargets, u: float) -> None:
    """RTX CAD — 하위 Gprim 을 점진적으로 invisible (실제로 보이는 fade)."""
    paths = targets.sorted_gprim_paths
    n = len(paths)
    if n <= 0:
        return
    u = _smoothstep01(u)
    hide_n = n if u >= 1.0 else min(n, int(u * n))
    for i, p in enumerate(paths):
        _set_visible_immediate(p, i >= hide_n)


def _apply_progressive_show(targets: _FadeTargets, u: float) -> None:
    paths = targets.sorted_gprim_paths
    n = len(paths)
    if n <= 0:
        return
    u = _smoothstep01(u)
    show_n = n if u >= 1.0 else min(n, int(u * n))
    for i, p in enumerate(paths):
        _set_visible_immediate(p, i < show_n)


def _show_all_gprims_under(targets: _FadeTargets) -> None:
    for p in targets.gprim_paths:
        _set_visible_immediate(p, True)


def _reset_mdl_opacity_for_show(targets: _FadeTargets) -> None:
    """Play fade 등으로 session 에 남은 opacity override 를 제거(체크 해제 시 강제 표시)."""
    stage = _get_stage()
    if stage is None or not targets.shader_slots:
        return
    try:
        with _session_edit(stage):
            for slot in targets.shader_slots:
                prim = stage.GetPrimAtPath(slot.shader_path)
                if not prim or not prim.IsValid():
                    continue
                shader = UsdShade.Shader(prim)
                inp = shader.GetInput(slot.input_name)
                if inp:
                    try:
                        inp.Clear()
                    except Exception:
                        pass
                if slot.enable_opacity_path:
                    en = shader.GetInput(slot.enable_opacity_path)
                    if en:
                        try:
                            en.Clear()
                        except Exception:
                            pass
    except Exception:
        pass


def _clear_mdl_fade_opacity(targets: _FadeTargets) -> None:
    stage = _get_stage()
    if stage is None:
        return
    try:
        with _session_edit(stage):
            for slot in targets.shader_slots:
                prim = stage.GetPrimAtPath(slot.shader_path)
                if not prim or not prim.IsValid():
                    continue
                shader = UsdShade.Shader(prim)
                inp = shader.GetInput(slot.input_name)
                if inp:
                    try:
                        if slot.had_authored and slot.original is not None:
                            inp.Set(float(slot.original))
                        else:
                            inp.Clear()
                    except Exception:
                        pass
                if slot.enable_opacity_path:
                    en = shader.GetInput(slot.enable_opacity_path)
                    if en:
                        try:
                            if slot.enable_was_authored and slot.enable_original is not None:
                                en.Set(bool(slot.enable_original))
                            else:
                                en.Clear()
                        except Exception:
                            pass
    except Exception:
        pass


def _play_start_nothing_visible_to_fade(path: str, targets: _FadeTargets) -> bool:
    """Play 시작 — 이미 전부 숨김이면 fade 없이 스킵."""
    if _prim_is_draw_visible(path):
        use_mdl = targets.fade_mode == "mdl" and bool(targets.shader_slots)
        if use_mdl:
            return _read_mdl_opacity(targets) <= 0.01
        return len(targets.sorted_gprim_paths) == 0
    return True


def _run_fade_on_update(
    *,
    duration_sec: float,
    to_visible: bool,
    targets: _FadeTargets,
    on_done: Callable[[], None],
    from_current_visibility: bool = False,
    usd_context_name: str = "",
) -> None:
    """main 스레드 — Kit update 마다 fade (mdl opacity 또는 progressive visibility)."""
    ctx = str(usd_context_name or "").strip() or _current_context_key()

    def _finish_hide() -> None:
        if use_mdl:
            _apply_mdl_fade_opacity(targets, 0.0)
            _clear_mdl_fade_opacity(targets)
        else:
            for p in targets.sorted_gprim_paths:
                _set_visible_immediate(p, False)
        _set_visible_immediate(path, False)
        _unsub()
        on_done()

    def _finish_show() -> None:
        if use_mdl:
            _apply_mdl_fade_opacity(targets, 1.0)
            _clear_mdl_fade_opacity(targets)
        else:
            _show_all_gprims_under(targets)
        _unsub()
        on_done()

    def _tick(_e=None) -> None:
        with play_prim_hide_stage_context(ctx or None):
            if use_mdl and int(box.get("warmup", 0)) < 3:
                box["warmup"] = int(box.get("warmup", 0)) + 1
                _preload_mdl_shader_inputs(targets.shader_slots)
                return
            elapsed = time.monotonic() - t0
            if elapsed >= duration_sec:
                if to_visible:
                    _finish_show()
                else:
                    _finish_hide()
                return
            u = elapsed / max(0.001, duration_sec)
            if use_mdl:
                if to_visible:
                    op = _smoothstep01(u)
                elif from_current_visibility:
                    start_op = float(box.get("mdl_start_op", 1.0) or 1.0)
                    op = start_op * (1.0 - _smoothstep01(u))
                else:
                    op = 1.0 - _smoothstep01(u)
                _apply_mdl_fade_opacity(targets, op)
            else:
                if to_visible:
                    _apply_progressive_show(targets, u)
                else:
                    _apply_progressive_hide(targets, u)

    with play_prim_hide_stage_context(ctx or None):
        stage = _get_stage()
        path = targets.root_path
        if stage is None or (
            not targets.gprim_paths and targets.fade_mode != "mdl"
        ):
            if to_visible:
                _set_visible_immediate(path, True)
            else:
                _capture_snapshot(path)
                _set_visible_immediate(path, False)
            on_done()
            return

        use_mdl = targets.fade_mode == "mdl" and bool(targets.shader_slots)
        mode_label = "mdl" if use_mdl else "progressive"

        if to_visible:
            _set_visible_immediate(path, True)
            if use_mdl:
                _apply_mdl_fade_opacity(targets, 0.0)
            else:
                for p in targets.sorted_gprim_paths:
                    _set_visible_immediate(p, False)
        else:
            _capture_snapshot(path)
            if from_current_visibility:
                if _play_start_nothing_visible_to_fade(path, targets):
                    _set_visible_immediate(path, False)
                    on_done()
                    return
                if use_mdl:
                    start_op = _read_mdl_opacity(targets)
                    _apply_mdl_fade_opacity(targets, start_op)
                else:
                    if not _prim_is_draw_visible(path):
                        _set_visible_immediate(path, False)
                        on_done()
                        return
            else:
                _set_visible_immediate(path, True)
                if use_mdl:
                    _apply_mdl_fade_opacity(targets, 1.0)
                else:
                    _show_all_gprims_under(targets)

        print(
            f"{_PRINT_PREFIX} fade {'show' if to_visible else 'hide'} ({mode_label}): "
            f"{len(targets.gprim_paths)} Gprim, {len(targets.shader_slots)} shader, "
            f"{duration_sec:.2f}s @ {path!r} ctx={ctx!r}"
            + (" from-current" if from_current_visibility and not to_visible else ""),
            flush=True,
        )

        t0 = time.monotonic()
        box: Dict[str, object] = {
            "sub": None,
            "warmup": 0,
            "mdl_start_op": _read_mdl_opacity(targets) if (from_current_visibility and not to_visible and use_mdl) else 1.0,
        }

        def _unsub() -> None:
            sub = box.get("sub")
            if sub is not None:
                try:
                    sub.unsubscribe()  # type: ignore[attr-defined]
                except Exception:
                    pass
            box["sub"] = None

        try:
            import omni.kit.app as _kapp  # type: ignore

            box["sub"] = _kapp.get_app().get_update_event_stream().create_subscription_to_pop(
                _tick,
                name="morph.lam_control_1.play_prim_hide.fade",
            )
        except Exception as exc:
            print(f"{_PRINT_PREFIX} fade update sub failed: {exc}", flush=True)
            if to_visible:
                _set_visible_immediate(path, True)
                _show_all_gprims_under(targets)
                _clear_mdl_fade_opacity(targets)
            else:
                _capture_snapshot(path)
                _set_visible_immediate(path, False)
                _clear_mdl_fade_opacity(targets)
            on_done()


def _start_play_hide_fade_chain(
    on_all_done: Callable[[], None],
    *,
    usd_context_name: str = "",
) -> None:
    """play_start — fade 항목을 순차 실행 (main)."""
    ctx = str(usd_context_name or "").strip() or _current_context_key()
    queue: List[tuple[str, float]] = []
    with play_prim_hide_stage_context(ctx or None):
        for spec in _load_specs():
            path = str(getattr(spec, "prim_path", "") or "").strip()
            if not path:
                continue
            if _resolve_fade_enabled(spec) and _resolve_fade_hide_in(spec):
                queue.append((path, _resolve_fade_duration(spec)))
            else:
                _capture_snapshot(path)
                _set_visible_immediate(path, False)

    if not queue:

        def _done_empty() -> None:
            with play_prim_hide_stage_context(ctx or None):
                print(f"{_PRINT_PREFIX} play_start: no fade queue", flush=True)
                _force_show_all_show_specs()
            on_all_done()

        _done_empty()
        return

    state = {"i": 0}

    def _run_next() -> None:
        with play_prim_hide_stage_context(ctx or None):
            i = state["i"]
            if i >= len(queue):
                print(
                    f"{_PRINT_PREFIX} play_start: fade done ({len(queue)} prim)",
                    flush=True,
                )
                _force_show_all_show_specs()
                on_all_done()
                return
            path, dur = queue[i]
            state["i"] = i + 1
            targets = _build_fade_targets(path, visible_gprims_only=True)
            if _play_start_nothing_visible_to_fade(path, targets):
                _capture_snapshot(path)
                _set_visible_immediate(path, False)
                _run_next()
                return
            _run_fade_on_update(
                duration_sec=dur,
                to_visible=False,
                targets=targets,
                on_done=_run_next,
                from_current_visibility=True,
                usd_context_name=ctx,
            )

    _run_next()


def _restore_all_specs() -> None:
    specs = _load_specs()
    paths = [str(getattr(s, "prim_path", "") or "").strip() for s in specs]
    paths = [p for p in paths if p]
    store = _snap_store()
    with _lock:
        snap = {p: store.pop(p, None) for p in paths}
    for path in paths:
        targets = _build_fade_targets(path)
        _clear_mdl_fade_opacity(targets)
        _show_all_gprims_under(targets)
        tok = snap.get(path)
        if tok is None:
            _set_visible_immediate(path, True)
        else:
            _apply_visibility_token(path, tok)


def _hide_all_instant(*, snapshot_restore: Optional[str] = None) -> None:
    for spec in _load_specs():
        path = str(getattr(spec, "prim_path", "") or "").strip()
        if not path:
            continue
        if snapshot_restore is not None:
            _snap_store()[path] = str(snapshot_restore)
        else:
            _capture_snapshot(path)
        targets = _build_fade_targets(path)
        _clear_mdl_fade_opacity(targets)
        _set_visible_immediate(path, False)


def _force_show_all_specs() -> None:
    """체크박스 해제(ui_show) — snapshot·fade 잔여와 무관하게 항상 표시."""
    specs = _load_specs()
    paths = [str(getattr(s, "prim_path", "") or "").strip() for s in specs]
    paths = [p for p in paths if p]
    store = _snap_store()
    for p in paths:
        store.pop(p, None)
    for path in paths:
        targets = _build_fade_targets(path)
        _set_visible_immediate(path, True)
        _show_all_gprims_under(targets)
        if targets.shader_slots:
            _apply_mdl_fade_opacity(targets, 1.0)
        _reset_mdl_opacity_for_show(targets)


def _force_show_all_show_specs() -> None:
    """재생/체크 ON 시 show 목록 prim 은 항상 보이게."""
    specs = _load_show_specs()
    paths = [str(getattr(s, "prim_path", "") or "").strip() for s in specs]
    paths = [p for p in paths if p]
    for path in paths:
        targets = _build_fade_targets(path)
        _set_visible_immediate(path, True)
        _show_all_gprims_under(targets)
        if targets.shader_slots:
            _apply_mdl_fade_opacity(targets, 1.0)
        _reset_mdl_opacity_for_show(targets)


def _hide_all_show_specs_instant(*, snapshot_restore: Optional[str] = None) -> None:
    """체크 OFF 시 show 목록은 반대로 숨김."""
    for spec in _load_show_specs():
        path = str(getattr(spec, "prim_path", "") or "").strip()
        if not path:
            continue
        if snapshot_restore is not None:
            _snap_store()[path] = str(snapshot_restore)
        else:
            _capture_snapshot(path)
        targets = _build_fade_targets(path)
        _clear_mdl_fade_opacity(targets)
        _set_visible_immediate(path, False)


def _restore_all_show_specs() -> None:
    """stop_reset 복원: show 목록도 원래 visibility 로 돌림."""
    specs = _load_show_specs()
    paths = [str(getattr(s, "prim_path", "") or "").strip() for s in specs]
    paths = [p for p in paths if p]
    store = _snap_store()
    with _lock:
        snap = {p: store.pop(p, None) for p in paths}
    for path in paths:
        targets = _build_fade_targets(path)
        _clear_mdl_fade_opacity(targets)
        _show_all_gprims_under(targets)
        tok = snap.get(path)
        if tok is None:
            _set_visible_immediate(path, True)
        else:
            _apply_visibility_token(path, tok)


def _show_all_instant() -> None:
    for spec in _load_specs():
        path = str(getattr(spec, "prim_path", "") or "").strip()
        if not path:
            continue
        targets = _build_fade_targets(path)
        _clear_mdl_fade_opacity(targets)
        _show_all_gprims_under(targets)
        tok = _snap_store().get(path)
        if tok is not None:
            _apply_visibility_token(path, tok)
        else:
            _set_visible_immediate(path, True)


def _apply_phase_instant(phase: str) -> None:
    specs = _load_specs()
    show_specs = _load_show_specs()
    if not specs and not show_specs:
        return

    if phase == "play_start":
        # hide fade 는 apply_play_prim_hide_phase() 경로에서 처리됨.
        if specs and _any_spec_fade_for_hide():
            return
        if specs:
            _hide_all_instant()
            print(
                f"{_PRINT_PREFIX} play_start: hid {len(specs)} prim(s)",
                flush=True,
            )
        if show_specs:
            _force_show_all_show_specs()
        return

    if phase == "play_stop_reset":
        try:
            from .lam_viewport_overlay_config import (
                PLAY_HIDE_RESTORE_VISIBLE_ON_STOP_RESET,
            )

            restore_cfg = bool(PLAY_HIDE_RESTORE_VISIBLE_ON_STOP_RESET)
        except Exception:
            restore_cfg = True
        hide_checked_override = _hide_checked_var.get()
        if hide_checked_override is not None:
            hide_checked = bool(hide_checked_override)
        else:
            try:
                from .lam_viewport_overlay_state import get_toggle_play_prim_hide

                hide_checked = bool(get_toggle_play_prim_hide())
            except Exception:
                hide_checked = False
        restore = bool(restore_cfg) and not hide_checked
        if restore:
            _restore_all_specs()
            if show_specs:
                _restore_all_show_specs()
            print(f"{_PRINT_PREFIX} play_stop_reset: restored visibility", flush=True)
        elif hide_checked:
            # 체크 ON: show 목록은 항상 보이게 유지
            if show_specs:
                _force_show_all_show_specs()
            print(
                f"{_PRINT_PREFIX} play_stop_reset: keep hidden (prim숨김 checked)",
                flush=True,
            )
        else:
            # 체크 OFF + restore 비활성: UI 정책대로 show 목록은 숨김
            if show_specs:
                _hide_all_show_specs_instant(snapshot_restore="inherited")
            print(
                f"{_PRINT_PREFIX} play_stop_reset: keep hidden (restore disabled)",
                flush=True,
            )
        return

    if phase == "ui_hide":
        # 해제 시 항상 보이게 — 이미 invisible 인 상태를 snapshot 에 남기지 않음
        _hide_all_instant(snapshot_restore="inherited")
        if show_specs:
            _force_show_all_show_specs()
        found, total = prim_hide_specs_stage_status()
        if total > 0:
            print(
                f"{_PRINT_PREFIX} ui_hide: hid on stage {found}/{total} spec(s)",
                flush=True,
            )
        return

    if phase == "ui_show":
        _force_show_all_specs()
        if show_specs:
            _hide_all_show_specs_instant(snapshot_restore="inherited")
        found, total = prim_hide_specs_stage_status()
        if total > 0:
            print(
                f"{_PRINT_PREFIX} ui_show: force show on stage {found}/{total} spec(s)",
                flush=True,
            )
        return

    print(f"{_PRINT_PREFIX} unknown phase: {phase}", flush=True)


__all__ = [
    "PlayHidePhase",
    "apply_play_prim_hide_phase",
    "apply_play_prim_hide_phase_for_context",
    "apply_play_prim_hide_ui_instant",
    "apply_play_prim_hide_ui_instant_for_context",
    "kickoff_play_prim_hide_play_start",
    "planned_play_prim_hide_duration_sec",
    "play_prim_hide_specs_configured",
    "play_prim_hide_stage_context",
    "prim_hide_specs_stage_status",
]
