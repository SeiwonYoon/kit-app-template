"""EP 포트 수(2/3)에 따른 Master USD 위 prim show/hide — USD 재로드 없음."""

from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional, Tuple

import omni.usd as ou  # type: ignore
from pxr import UsdGeom  # type: ignore

from .sim_control_defaults import SIM_CONTROL_DEFAULTS as _SIM_DEF

_PRINT_PREFIX = "[TBS/EPVis]"

_lock = threading.Lock()
_baseline_snapshot: Dict[str, str] = {}
_baseline_by_scope: Dict[str, Dict[str, str]] = {}
_active_ep_count: Optional[int] = None
_active_ep_by_scope: Dict[str, int] = {}
_active_ebs_state_by_scope: Dict[str, Tuple[int, bool]] = {}
_apply_retry_sub: Any = None


def ep_count_from_combo_idx(idx: int) -> int:
    """시뮬 EP 콤보: 0 → EP2, 1 → EP3."""
    return 3 if int(idx) == 1 else 2


def ep_count_idx_for_screen(ext: Any, screen_1based: int) -> int:
    """화면별 EP 콤보 인덱스 (0=EP2, 1=EP3) — CASE A/B 실시간 UI."""
    try:
        si = max(1, int(screen_1based))
    except Exception:
        si = 1
    try:
        from .ebs_case_models import case_from_screen, get_sim_ep_count_idx_for_case

        return int(get_sim_ep_count_idx_for_case(ext, case_from_screen(si)))
    except Exception:
        return int(_SIM_DEF.ep_count_idx)


def ebs_enabled_for_screen(ext: Any, screen_1based: int) -> bool:
    """화면별 EBS 적용 여부 — CASE A/B 실시간 UI."""
    try:
        si = max(1, int(screen_1based))
    except Exception:
        si = 1
    try:
        from .ebs_case_models import case_from_screen, get_sim_ebs_enabled_for_case

        return bool(get_sim_ebs_enabled_for_case(ext, case_from_screen(si)))
    except Exception:
        return True


def _layout_for_ep_count(ep_count: int):
    from .tbs_usd_window import EP2_PORT_LAYOUT, EP3_PORT_LAYOUT

    return EP3_PORT_LAYOUT if int(ep_count) >= 3 else EP2_PORT_LAYOUT


def _layout_for_ebs(ep_count: int, ebs_enabled: bool):
    from .tbs_usd_window import (
        EBS2_HIDE_LAYOUT,
        EBS2_SHOW_LAYOUT,
        EBS3_HIDE_LAYOUT,
        EBS3_SHOW_LAYOUT,
    )

    if int(ep_count) >= 3:
        return EBS3_SHOW_LAYOUT if bool(ebs_enabled) else EBS3_HIDE_LAYOUT
    return EBS2_SHOW_LAYOUT if bool(ebs_enabled) else EBS2_HIDE_LAYOUT


def _apply_ebs_layout_on_stage(
    stage: Any,
    ep_count: int,
    ebs_enabled: bool,
    *,
    scope_key: str,
    reason: str = "",
) -> bool:
    """EP 레이아웃 이후 EP2/3 × EBS ON/OFF 추가 show·hide."""
    if stage is None:
        return False
    sk = str(scope_key)
    ep_n = 3 if int(ep_count) >= 3 else 2
    ebs_on = bool(ebs_enabled)
    layout = _layout_for_ebs(ep_n, ebs_on)
    with _lock:
        prev = _active_ebs_state_by_scope.get(sk)
    if prev is not None:
        prev_ep, prev_ebs = int(prev[0]), bool(prev[1])
        if prev_ep != ep_n or prev_ebs != ebs_on:
            prev_layout = _layout_for_ebs(prev_ep, prev_ebs)
            for path in _unique_paths(prev_layout.show_prims):
                _restore_baseline(stage, path, scope_key=sk)
    hide_paths = _unique_paths(layout.hide_prims)
    show_paths = _unique_paths(layout.show_prims)
    hid_ok = 0
    show_ok = 0
    for path in hide_paths:
        if _set_visible_on_stage(stage, path, False, scope_key=sk):
            hid_ok += 1
    for path in show_paths:
        if _set_visible_on_stage(stage, path, True, scope_key=sk):
            show_ok += 1
    with _lock:
        _active_ebs_state_by_scope[sk] = (int(ep_n), bool(ebs_on))
    note = f" ({reason})" if reason else ""
    print(
        f"{_PRINT_PREFIX} EBS EP={ep_n} {'ON' if ebs_on else 'OFF'} scope={sk}{note}: "
        f"hide {hid_ok}/{len(hide_paths)}, show {show_ok}/{len(show_paths)}",
        flush=True,
    )
    return bool(hid_ok + show_ok > 0 or (not hide_paths and not show_paths))


def _default_scope_key() -> str:
    return "__default__"


def _scope_key_for_stage(stage: Any) -> str:
    if stage is None:
        return _default_scope_key()
    try:
        lyr = stage.GetRootLayer()
        if lyr is not None:
            ident = str(getattr(lyr, "identifier", "") or getattr(lyr, "realPath", "") or "").strip()
            if ident:
                return ident
    except Exception:
        pass
    return f"stage:{id(stage)}"


def _scope_key_for_context_name(context_name: Optional[str]) -> str:
    cn = str(context_name or "").strip()
    return _default_scope_key() if not cn else f"ctx:{cn}"


def _get_stage(context_name: Optional[str] = None):
    cn = str(context_name or "").strip()
    if cn:
        try:
            ctx = ou.get_context(cn)
            return ctx.get_stage() if ctx else None
        except Exception:
            return None
    ctx = ou.get_context()
    return ctx.get_stage() if ctx else None


def _baseline_map(scope_key: str) -> Dict[str, str]:
    if scope_key == _default_scope_key():
        return _baseline_snapshot
    with _lock:
        m = _baseline_by_scope.get(scope_key)
        if m is None:
            m = {}
            _baseline_by_scope[scope_key] = m
        return m


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


def _capture_baseline(stage: Any, path: str, *, scope_key: str) -> None:
    baseline = _baseline_map(scope_key)
    with _lock:
        if path in baseline:
            return
    if stage is None:
        return
    prim = stage.GetPrimAtPath(path)
    if not prim or not prim.IsValid():
        return
    img = UsdGeom.Imageable(prim)
    if not img:
        return
    tok = _visibility_token(img)
    with _lock:
        if path not in baseline:
            baseline[path] = tok


def _apply_token_on_stage(stage: Any, path: str, token: str) -> bool:
    if stage is None:
        return False
    prim = stage.GetPrimAtPath(path)
    if not prim or not prim.IsValid():
        return False
    img = UsdGeom.Imageable(prim)
    if not img:
        return False
    try:
        if token == "invisible":
            img.MakeInvisible()
        else:
            img.GetVisibilityAttr().Set(UsdGeom.Tokens.inherited)
            img.MakeVisible()
        return True
    except Exception as exc:
        print(f"{_PRINT_PREFIX} apply({path}) failed: {exc}", flush=True)
        return False


def _restore_baseline(stage: Any, path: str, *, scope_key: str) -> None:
    baseline = _baseline_map(scope_key)
    with _lock:
        tok = baseline.get(path, "inherited")
    _apply_token_on_stage(stage, path, tok)


def _set_visible_on_stage(stage: Any, path: str, visible: bool, *, scope_key: str) -> bool:
    path = str(path or "").strip()
    if not path:
        return False
    _capture_baseline(stage, path, scope_key=scope_key)
    return _apply_token_on_stage(stage, path, "inherited" if visible else "invisible")


def _coerce_path_tuple(obj: Any) -> Tuple[str, ...]:
    """
    EpPortLayout hide_prims/show_prims → 경로 튜플.

    ``("/World/x")`` 처럼 쉼표 없이 쓰면 Python 에서 str 이 되므로
    문자 단위 순회를 막기 위해 str 은 경로 1개 튜플로 본다.
    """
    if isinstance(obj, str):
        s = str(obj).strip()
        return (s,) if s else ()
    if obj is None:
        return ()
    try:
        return tuple(obj)
    except TypeError:
        s = str(obj).strip()
        return (s,) if s else ()


def _unique_paths(paths: Any) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for raw in _coerce_path_tuple(paths):
        p = str(raw or "").strip()
        if not p or p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out


def apply_ep_port_layout_on_stage(
    stage: Any,
    ep_count: int,
    *,
    scope_key: Optional[str] = None,
    ebs_enabled: bool = True,
    reason: str = "",
) -> bool:
    """지정 stage 에 EP2/EP3 show·hide 를 적용한다."""
    global _active_ep_count
    if stage is None:
        return False
    sk = str(scope_key or _scope_key_for_stage(stage))
    layout = _layout_for_ep_count(ep_count)
    with _lock:
        prev = _active_ep_by_scope.get(sk)
        if sk == _default_scope_key() and prev is None:
            prev = _active_ep_count
    note = f" ({reason})" if reason else ""

    if prev is not None and int(prev) != int(ep_count):
        prev_layout = _layout_for_ep_count(int(prev))
        for path in _unique_paths(prev_layout.show_prims):
            _restore_baseline(stage, path, scope_key=sk)

    hide_paths = _unique_paths(layout.hide_prims)
    show_paths = _unique_paths(layout.show_prims)
    hid_ok = 0
    show_ok = 0
    for path in hide_paths:
        if _set_visible_on_stage(stage, path, False, scope_key=sk):
            hid_ok += 1
    for path in show_paths:
        if _set_visible_on_stage(stage, path, True, scope_key=sk):
            show_ok += 1

    with _lock:
        _active_ep_by_scope[sk] = int(ep_count)
        if sk == _default_scope_key():
            _active_ep_count = int(ep_count)

    print(
        f"{_PRINT_PREFIX} EP={ep_count} scope={sk}{note}: hide {hid_ok}/{len(hide_paths)}, "
        f"show {show_ok}/{len(show_paths)}",
        flush=True,
    )
    _apply_ebs_layout_on_stage(
        stage,
        int(ep_count),
        bool(ebs_enabled),
        scope_key=sk,
        reason=reason or "ebs",
    )
    return bool(hid_ok + show_ok > 0 or (not hide_paths and not show_paths))


def apply_ep_port_layout(ep_count: int, *, ebs_enabled: bool = True, reason: str = "") -> bool:
    """기본 ``omni.usd`` 컨텍스트 stage 에 EP2/EP3 show·hide 적용."""
    stage = _get_stage()
    return apply_ep_port_layout_on_stage(
        stage,
        ep_count,
        scope_key=_default_scope_key(),
        ebs_enabled=bool(ebs_enabled),
        reason=reason,
    )


def apply_ep_port_layout_for_context(
    ext: Any,
    context_name: str,
    screen_1based: int,
    *,
    reason: str = "",
) -> bool:
    """분할 보조 USD 컨텍스트에 화면별 EP2/EP3 레이아웃을 적용한다."""
    cn = str(context_name or "").strip()
    if not cn:
        return False
    stage = _get_stage(cn)
    if stage is None:
        return False
    ep_count = ep_count_from_combo_idx(ep_count_idx_for_screen(ext, int(screen_1based)))
    ebs_on = ebs_enabled_for_screen(ext, int(screen_1based))
    return apply_ep_port_layout_on_stage(
        stage,
        ep_count,
        scope_key=_scope_key_for_context_name(cn),
        ebs_enabled=bool(ebs_on),
        reason=reason or f"split_screen{int(screen_1based)}",
    )


def schedule_apply_ep_port_layout_for_context(
    ext: Any,
    context_name: str,
    screen_1based: int,
    *,
    delay_frames: int = 4,
    max_attempts: int = 60,
    reason: str = "",
) -> None:
    """보조 컨텍스트 stage 가 늦게 열릴 때 EP/EBS show·hide 재시도."""
    cn = str(context_name or "").strip()
    if not cn:
        return
    # 즉시 1회 시도
    try:
        if apply_ep_port_layout_for_context(
            ext, cn, int(screen_1based), reason=reason
        ):
            return
    except Exception:
        pass
    frames_until = [max(0, int(delay_frames))]
    attempts_left = [max(1, int(max_attempts))]
    sub_holder: list = [None]

    def _stop() -> None:
        sub = sub_holder[0]
        if sub is None:
            return
        try:
            sub.unsubscribe()
        except Exception:
            pass
        sub_holder[0] = None

    def _tick(_e=None) -> None:
        if frames_until[0] > 0:
            frames_until[0] -= 1
            return
        stage = _get_stage(cn)
        if stage is not None:
            try:
                ep_count = ep_count_from_combo_idx(
                    ep_count_idx_for_screen(ext, int(screen_1based))
                )
                ebs_on = ebs_enabled_for_screen(ext, int(screen_1based))
                apply_ep_port_layout_on_stage(
                    stage,
                    ep_count,
                    scope_key=_scope_key_for_context_name(cn),
                    ebs_enabled=bool(ebs_on),
                    reason=reason or f"split_screen{int(screen_1based)}_sched",
                )
            except Exception:
                pass
            _stop()
            return
        attempts_left[0] -= 1
        if attempts_left[0] <= 0:
            _stop()
            return
        frames_until[0] = 2

    try:
        import omni.kit.app

        sub_holder[0] = (
            omni.kit.app.get_app()
            .get_update_event_stream()
            .create_subscription_to_pop(_tick, name="tbs.ep_layout_ctx")
        )
    except Exception:
        pass


def _stop_retry_subscription() -> None:
    global _apply_retry_sub
    if _apply_retry_sub is None:
        return
    try:
        _apply_retry_sub.unsubscribe()
    except Exception:
        pass
    _apply_retry_sub = None


def schedule_apply_ep_port_layout(
    ext: Any,
    ep_count: int,
    *,
    ebs_enabled: Optional[bool] = None,
    delay_frames: int = 24,
    max_attempts: int = 120,
    reason: str = "",
) -> None:
    """Master open·startup 후 stage prim 준비될 때까지 post_update 재시도."""
    if ebs_enabled is None:
        try:
            from .ebs_control_panel_ui import get_sim_ebs_enabled

            ebs_enabled = bool(get_sim_ebs_enabled(ext))
        except Exception:
            ebs_enabled = True
    ebs_flag = bool(ebs_enabled)
    _stop_retry_subscription()
    frames_until = [max(0, int(delay_frames))]
    attempts_left = [max(1, int(max_attempts))]

    def _finish() -> None:
        _stop_retry_subscription()

    def _tick(_e=None) -> None:
        if frames_until[0] > 0:
            frames_until[0] -= 1
            return
        layout = _layout_for_ep_count(ep_count)
        ebs_layout = _layout_for_ebs(ep_count, ebs_flag)
        paths = _unique_paths(
            _coerce_path_tuple(layout.hide_prims)
            + _coerce_path_tuple(layout.show_prims)
            + _coerce_path_tuple(ebs_layout.hide_prims)
            + _coerce_path_tuple(ebs_layout.show_prims)
        )

        if paths:
            stage = _get_stage()
            if stage is None:
                attempts_left[0] -= 1
                if attempts_left[0] <= 0:
                    print(f"{_PRINT_PREFIX} schedule gave up (no stage)", flush=True)
                    _finish()
                return
            found = sum(
                1
                for p in paths
                if stage.GetPrimAtPath(p) and stage.GetPrimAtPath(p).IsValid()
            )
            if found <= 0:
                attempts_left[0] -= 1
                if attempts_left[0] <= 0:
                    print(
                        f"{_PRINT_PREFIX} schedule gave up (prim not ready) EP={ep_count}",
                        flush=True,
                    )
                    _finish()
                return
        apply_ep_port_layout(ep_count, ebs_enabled=ebs_flag, reason=reason)
        fn = getattr(ext, "_sync_sim_multi_split_row_visibility_fn", None)
        if callable(fn):
            try:
                fn(ext)
            except Exception:
                pass
        _finish()

    try:
        import omni.kit.app as _app  # type: ignore

        stream = _app.get_app().get_post_update_event_stream()
        global _apply_retry_sub
        _apply_retry_sub = stream.create_subscription_to_pop(
            _tick,
            name="morph.tbs_control_2.ep_port_visibility.apply",
        )
    except Exception as exc:
        print(f"{_PRINT_PREFIX} schedule failed: {exc}", flush=True)
        apply_ep_port_layout(ep_count, ebs_enabled=ebs_flag, reason=reason)


def on_sim_ebs_enabled_changed(ext: Any) -> None:
    """EBS 적용여부 체크 변경 — CASE A(화면1) 호환 래퍼."""
    try:
        from .control_window import on_sim_ebs_enabled_changed_for_case
        from .ebs_case_models import CASE_A

        on_sim_ebs_enabled_changed_for_case(ext, CASE_A)
    except Exception:
        pass


def on_sim_ep_count_combo_changed(ext: Any) -> None:
    """``on_sim_ep_count_changed`` 마지막 — EP 콤보 변경 시 visibility 전환 (USD 재오픈 없음)."""
    try:
        from .ebs_control_panel_ui import get_sim_ep_count_idx

        idx = int(get_sim_ep_count_idx(ext))
    except Exception:
        try:
            idx = int(ext._sim_ep_count_combo.model.get_item_value_model().as_int)
        except Exception:
            idx = 0
    last_idx = getattr(ext, "_ep_port_visibility_combo_idx", None)
    if last_idx is not None and int(last_idx) == int(idx):
        return
    ext._ep_port_visibility_combo_idx = int(idx)
    ep_count = ep_count_from_combo_idx(idx)
    try:
        from .ebs_control_panel_ui import get_sim_ebs_enabled

        ebs_on = bool(get_sim_ebs_enabled(ext))
    except Exception:
        ebs_on = True
    schedule_apply_ep_port_layout(
        ext,
        ep_count,
        ebs_enabled=ebs_on,
        delay_frames=2,
        reason="ep_count_changed",
    )


def teardown_ep_port_visibility(_ext: Any = None) -> None:
    _stop_retry_subscription()


__all__ = [
    "ep_count_from_combo_idx",
    "ep_count_idx_for_screen",
    "ebs_enabled_for_screen",
    "apply_ep_port_layout",
    "apply_ep_port_layout_for_context",
    "apply_ep_port_layout_on_stage",
    "schedule_apply_ep_port_layout",
    "schedule_apply_ep_port_layout_for_context",
    "on_sim_ep_count_combo_changed",
    "on_sim_ebs_enabled_changed",
    "teardown_ep_port_visibility",
]
