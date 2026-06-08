"""EP 포트 수(2/3)에 따른 Master USD 위 prim show/hide — USD 재로드 없음."""

from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional, Tuple

import omni.usd as ou  # type: ignore
from pxr import UsdGeom  # type: ignore

_PRINT_PREFIX = "[TBS/EPVis]"

_lock = threading.Lock()
_baseline_snapshot: Dict[str, str] = {}
_active_ep_count: Optional[int] = None
_apply_retry_sub: Any = None


def ep_count_from_combo_idx(idx: int) -> int:
    """시뮬 EP 콤보: 0 → EP2, 1 → EP3."""
    return 3 if int(idx) == 1 else 2


def _layout_for_ep_count(ep_count: int):
    from .tbs_usd_window import EP2_PORT_LAYOUT, EP3_PORT_LAYOUT

    return EP3_PORT_LAYOUT if int(ep_count) >= 3 else EP2_PORT_LAYOUT


def _get_stage():
    ctx = ou.get_context()
    return ctx.get_stage() if ctx else None


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


def _capture_baseline(path: str) -> None:
    with _lock:
        if path in _baseline_snapshot:
            return
    stage = _get_stage()
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
        if path not in _baseline_snapshot:
            _baseline_snapshot[path] = tok


def _apply_token(path: str, token: str) -> bool:
    stage = _get_stage()
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


def _restore_baseline(path: str) -> None:
    with _lock:
        tok = _baseline_snapshot.get(path, "inherited")
    _apply_token(path, tok)


def _set_visible(path: str, visible: bool) -> bool:
    path = str(path or "").strip()
    if not path:
        return False
    _capture_baseline(path)
    return _apply_token(path, "inherited" if visible else "invisible")


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


def apply_ep_port_layout(ep_count: int, *, reason: str = "") -> bool:
    """이전 EP show prim 초기화 → 새 EP hide/show 적용."""
    global _active_ep_count
    layout = _layout_for_ep_count(ep_count)
    prev = _active_ep_count
    note = f" ({reason})" if reason else ""

    if prev is not None and int(prev) != int(ep_count):
        prev_layout = _layout_for_ep_count(int(prev))
        for path in _unique_paths(prev_layout.show_prims):
            _restore_baseline(path)

    hide_paths = _unique_paths(layout.hide_prims)
    show_paths = _unique_paths(layout.show_prims)
    hid_ok = 0
    show_ok = 0
    for path in hide_paths:
        if _set_visible(path, False):
            hid_ok += 1
    for path in show_paths:
        if _set_visible(path, True):
            show_ok += 1

    with _lock:
        _active_ep_count = int(ep_count)

    print(
        f"{_PRINT_PREFIX} EP={ep_count}{note}: hide {hid_ok}/{len(hide_paths)}, "
        f"show {show_ok}/{len(show_paths)}",
        flush=True,
    )
    return bool(hid_ok + show_ok > 0 or (not hide_paths and not show_paths))


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
            name="morph.tbs_control_1.ep_port_visibility.apply",
        )
    except Exception as exc:
        print(f"{_PRINT_PREFIX} schedule failed: {exc}", flush=True)
        apply_ep_port_layout(ep_count, reason=reason)


def on_sim_ep_count_combo_changed(ext: Any) -> None:
    """``on_sim_ep_count_changed`` 마지막 — EP 콤보 변경 시 visibility 전환 (USD 재오픈 없음)."""
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
    "apply_ep_port_layout",
    "schedule_apply_ep_port_layout",
    "on_sim_ep_count_combo_changed",
    "teardown_ep_port_visibility",
]
