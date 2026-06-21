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
_apply_retry_sub: Any = None


def ep_count_from_combo_idx(idx: int) -> int:
    """시뮬 EP 콤보: 0 → EP2, 1 → EP3."""
    return 3 if int(idx) == 1 else 2


def ep_count_idx_for_screen(ext: Any, screen_1based: int) -> int:
    """
    화면별 EP 콤보 인덱스 (0=EP2, 1=EP3).

    멀티 분할 시 화면별 「현재 설정 저장」스냅샷이 있으면 그 값을 쓰고,
    없으면 화면1 스냅샷(또는 현재 UI)을 따른다.
    """
    try:
        si = max(1, int(screen_1based))
    except Exception:
        si = 1
    try:
        snaps = list(getattr(ext, "_sim_per_screen_snapshots", None) or [])
        idx = si - 1
        if 0 <= idx < len(snaps) and isinstance(snaps[idx], dict):
            return int(snaps[idx].get("ep_count_idx", _SIM_DEF.ep_count_idx) or _SIM_DEF.ep_count_idx)
        if si > 1 and len(snaps) >= 1 and isinstance(snaps[0], dict):
            return int(snaps[0].get("ep_count_idx", _SIM_DEF.ep_count_idx) or _SIM_DEF.ep_count_idx)
    except Exception:
        pass
    try:
        from .ebs_control_panel_ui import get_sim_ep_count_idx

        return int(get_sim_ep_count_idx(ext))
    except Exception:
        return int(_SIM_DEF.ep_count_idx)


def _layout_for_ep_count(ep_count: int):
    from .tbs_usd_window import EP2_PORT_LAYOUT, EP3_PORT_LAYOUT

    return EP3_PORT_LAYOUT if int(ep_count) >= 3 else EP2_PORT_LAYOUT


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


def _unique_paths(paths: Tuple[str, ...]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for raw in paths:
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
    return bool(hid_ok + show_ok > 0 or (not hide_paths and not show_paths))


def apply_ep_port_layout(ep_count: int, *, reason: str = "") -> bool:
    """기본 ``omni.usd`` 컨텍스트 stage 에 EP2/EP3 show·hide 적용."""
    stage = _get_stage()
    return apply_ep_port_layout_on_stage(
        stage,
        ep_count,
        scope_key=_default_scope_key(),
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
    return apply_ep_port_layout_on_stage(
        stage,
        ep_count,
        scope_key=_scope_key_for_context_name(cn),
        reason=reason or f"split_screen{int(screen_1based)}",
    )


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
    delay_frames: int = 24,
    max_attempts: int = 120,
    reason: str = "",
) -> None:
    """Master open·startup 후 stage prim 준비될 때까지 post_update 재시도."""
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
        paths = _unique_paths(layout.hide_prims + layout.show_prims)
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
        apply_ep_port_layout(ep_count, reason=reason)
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
        apply_ep_port_layout(ep_count, reason=reason)


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
    schedule_apply_ep_port_layout(
        ext,
        ep_count,
        delay_frames=2,
        reason="ep_count_changed",
    )


def teardown_ep_port_visibility(_ext: Any = None) -> None:
    _stop_retry_subscription()


__all__ = [
    "ep_count_from_combo_idx",
    "ep_count_idx_for_screen",
    "apply_ep_port_layout",
    "apply_ep_port_layout_for_context",
    "apply_ep_port_layout_on_stage",
    "schedule_apply_ep_port_layout",
    "on_sim_ep_count_combo_changed",
    "teardown_ep_port_visibility",
]
