"""CSV Play · 「prim숨김」 prim visibility (설정: lam_viewport_overlay_config).

Fade 는 **update 이벤트(프레임)** 마다 opacity 를 갱신한다.
(main 스레드 sleep 만 쓰면 뷰포트가 안 그려져 duration 후 한 번에 사라짐)
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Literal, Optional, Tuple

import omni.usd as ou  # type: ignore  # noqa: E402
from pxr import Sdf, Usd, UsdGeom, UsdShade  # type: ignore  # noqa: E402

_PRINT_PREFIX = "[LAM/PlayPrimHide]"

PlayHidePhase = Literal["play_start", "play_stop_reset", "ui_hide", "ui_show"]

_lock = threading.Lock()
_visibility_snapshot: Dict[str, str] = {}

_UI_PHASES = frozenset({"ui_hide", "ui_show"})


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
        if phase_s in _UI_PHASES:
            _schedule_instant_on_main(phase_s)
            return True
        if phase_s == "play_start" and _any_spec_fade_for_hide():
            return _apply_play_start_fade_async()
        return _run_instant_on_main_wait(phase_s)
    except Exception as exc:
        print(f"{_PRINT_PREFIX} phase={phase_s} failed: {exc}", flush=True)
        return False


def _smoothstep01(t: float) -> float:
    t = max(0.0, min(1.0, float(t)))
    return t * t * (3.0 - 2.0 * t)


def _schedule_instant_on_main(phase: str) -> None:
    try:
        from .lam_sequence_engine import _dispatch_main

        _dispatch_main(lambda: _apply_phase_instant(phase))
    except Exception:
        _apply_phase_instant(phase)


def _run_instant_on_main_wait(phase: str) -> bool:
    err: List[Optional[BaseException]] = [None]

    def _run() -> None:
        try:
            _apply_phase_instant(phase)
        except BaseException as e:
            err[0] = e
            raise

    try:
        from .lam_sequence_engine import _dispatch_main_wait

        ok = _dispatch_main_wait(_run, timeout=8.0)
    except Exception:
        _apply_phase_instant(phase)
        ok = True
    if err[0] is not None:
        print(f"{_PRINT_PREFIX} phase={phase} failed: {err[0]}", flush=True)
    return bool(ok)


def _apply_play_start_fade_async() -> bool:
    """fade: main 에서 update 구독 시작 → 호출 스레드는 완료까지 wait (렌더는 계속)."""
    total = sum(
        _resolve_fade_duration(s)
        for s in _load_specs()
        if _resolve_fade_enabled(s) and _resolve_fade_hide_in(s)
    )
    done = threading.Event()
    err: List[Optional[BaseException]] = [None]

    def _kickoff() -> None:
        try:
            _start_play_hide_fade_chain(lambda: done.set())
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
        _start_play_hide_fade_chain(lambda: done.set())

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
    ctx = ou.get_context()
    return ctx.get_stage() if ctx else None


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
        ctx = ou.get_context()
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


def _build_fade_targets(root_path: str) -> _FadeTargets:
    gpaths = _collect_gprim_paths(root_path)
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


def _capture_snapshot(path: str) -> None:
    with _lock:
        if path in _visibility_snapshot:
            return
    _, img = _get_imageable(path)
    if img is None:
        return
    tok = _visibility_token(img)
    with _lock:
        if path not in _visibility_snapshot:
            _visibility_snapshot[path] = tok


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


def _run_fade_on_update(
    *,
    duration_sec: float,
    to_visible: bool,
    targets: _FadeTargets,
    on_done: Callable[[], None],
) -> None:
    """main 스레드 — Kit update 마다 fade (mdl opacity 또는 progressive visibility)."""
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
        _set_visible_immediate(path, True)
        if use_mdl:
            _apply_mdl_fade_opacity(targets, 1.0)
        else:
            _show_all_gprims_under(targets)

    print(
        f"{_PRINT_PREFIX} fade {'show' if to_visible else 'hide'} ({mode_label}): "
        f"{len(targets.gprim_paths)} Gprim, {len(targets.shader_slots)} shader, "
        f"{duration_sec:.2f}s @ {path!r}",
        flush=True,
    )

    t0 = time.monotonic()
    box: Dict[str, object] = {"sub": None, "warmup": 0}

    def _unsub() -> None:
        sub = box.get("sub")
        if sub is not None:
            try:
                sub.unsubscribe()  # type: ignore[attr-defined]
            except Exception:
                pass
        box["sub"] = None

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
            op = _smoothstep01(u) if to_visible else (1.0 - _smoothstep01(u))
            _apply_mdl_fade_opacity(targets, op)
        else:
            if to_visible:
                _apply_progressive_show(targets, u)
            else:
                _apply_progressive_hide(targets, u)

    try:
        import omni.kit.app as _kapp  # type: ignore

        box["sub"] = _kapp.get_app().get_update_event_stream().create_subscription_to_pop(
            _tick,
            name="morph.lam_control.play_prim_hide.fade",
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


def _start_play_hide_fade_chain(on_all_done: Callable[[], None]) -> None:
    """play_start — fade 항목을 순차 실행 (main)."""
    queue: List[tuple[str, float]] = []
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
            print(f"{_PRINT_PREFIX} play_start: no fade queue", flush=True)
            on_all_done()

        _done_empty()
        return

    state = {"i": 0}

    def _run_next() -> None:
        i = state["i"]
        if i >= len(queue):
            print(
                f"{_PRINT_PREFIX} play_start: fade done ({len(queue)} prim)",
                flush=True,
            )
            on_all_done()
            return
        path, dur = queue[i]
        state["i"] = i + 1
        targets = _build_fade_targets(path)
        _run_fade_on_update(
            duration_sec=dur,
            to_visible=False,
            targets=targets,
            on_done=_run_next,
        )

    _run_next()


def _restore_all_specs() -> None:
    specs = _load_specs()
    paths = [str(getattr(s, "prim_path", "") or "").strip() for s in specs]
    paths = [p for p in paths if p]
    with _lock:
        snap = {p: _visibility_snapshot.pop(p, None) for p in paths}
    for path in paths:
        targets = _build_fade_targets(path)
        _clear_mdl_fade_opacity(targets)
        _show_all_gprims_under(targets)
        tok = snap.get(path)
        if tok is None:
            _set_visible_immediate(path, True)
        else:
            _apply_visibility_token(path, tok)


def _hide_all_instant() -> None:
    for spec in _load_specs():
        path = str(getattr(spec, "prim_path", "") or "").strip()
        if not path:
            continue
        _capture_snapshot(path)
        targets = _build_fade_targets(path)
        _clear_mdl_fade_opacity(targets)
        _set_visible_immediate(path, False)


def _show_all_instant() -> None:
    for spec in _load_specs():
        path = str(getattr(spec, "prim_path", "") or "").strip()
        if not path:
            continue
        targets = _build_fade_targets(path)
        _clear_mdl_fade_opacity(targets)
        _show_all_gprims_under(targets)
        with _lock:
            tok = _visibility_snapshot.get(path)
        if tok is not None:
            _apply_visibility_token(path, tok)
        else:
            _set_visible_immediate(path, True)


def _apply_phase_instant(phase: str) -> None:
    specs = _load_specs()
    if not specs:
        return

    if phase == "play_start":
        if _any_spec_fade_for_hide():
            return
        _hide_all_instant()
        print(f"{_PRINT_PREFIX} play_start: hid {len(specs)} prim(s)", flush=True)
        return

    if phase == "play_stop_reset":
        try:
            from .lam_viewport_overlay_config import (
                PLAY_HIDE_RESTORE_VISIBLE_ON_STOP_RESET,
            )

            restore = bool(PLAY_HIDE_RESTORE_VISIBLE_ON_STOP_RESET)
        except Exception:
            restore = True
        if restore:
            _restore_all_specs()
            print(f"{_PRINT_PREFIX} play_stop_reset: restored visibility", flush=True)
        else:
            print(
                f"{_PRINT_PREFIX} play_stop_reset: keep hidden (restore disabled)",
                flush=True,
            )
        return

    if phase == "ui_hide":
        _hide_all_instant()
        return

    if phase == "ui_show":
        _show_all_instant()
        return

    print(f"{_PRINT_PREFIX} unknown phase: {phase}", flush=True)


__all__ = ["PlayHidePhase", "apply_play_prim_hide_phase"]
