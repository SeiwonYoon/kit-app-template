"""LAM Viewport overlay 런타임 상태 저장소 (v1).

원칙:
- 재생 로직을 수정하지 않고, simulation_play의 스냅샷/콜백을 '관측'해서 상태를 만든다.
- UI는 이 모듈의 snapshot만 읽고 그린다.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple


@dataclass(frozen=True)
class FoupCounts:
    total: int = 25
    picked_count: int = 0
    placed_back_count: int = 0

    @property
    def waiting_count(self) -> int:
        return max(0, int(self.total) - int(self.picked_count))

    @property
    def done_count(self) -> int:
        return max(0, int(self.placed_back_count))

    @property
    def in_process_count(self) -> int:
        return max(0, int(self.picked_count) - int(self.placed_back_count))

    @property
    def current_in_foup_now(self) -> int:
        return max(0, int(self.total) - int(self.in_process_count))


_lock = threading.Lock()


def _startup_toggle_defaults() -> tuple[bool, bool, bool]:
    try:
        from .lam_viewport_overlay_config import (
            STARTUP_CHECK_DEVICE_LABELS,
            STARTUP_CHECK_FOUP_STATUS,
            STARTUP_CHECK_PICK_WHITELIST,
        )

        return (
            bool(STARTUP_CHECK_FOUP_STATUS),
            bool(STARTUP_CHECK_DEVICE_LABELS),
            bool(STARTUP_CHECK_PICK_WHITELIST),
        )
    except Exception:
        return True, True, False


_f0, _d0, _p0 = _startup_toggle_defaults()

# 토글(체크박스) 상태 — 시뮬창/HUD 동기화용
_toggle_foup_status: bool = _f0
_toggle_device_labels: bool = _d0
_toggle_pick_whitelist: bool = _p0

# 토글이 UI/모델 이벤트로 왕복하는 것을 막기 위한 디바운스(초단기 반전 무시)
_last_toggle_change_ts: Dict[str, float] = {"foup": 0.0, "device": 0.0, "pick": 0.0}
_last_toggle_change_val: Dict[str, bool] = {"foup": _f0, "device": _d0, "pick": _p0}

_startup_checkbox_side_effects_applied: bool = False

# 2D 상태 패널 수동 입력
_manual_eq_model: str = ""

# Current State: 일시정지/dwell 시에도 마지막 로그 유지
_last_state_title: str = ""

# 현재 선택된 CSV 파일 경로(문자열)
_selected_csv_path: str = ""

# simulation_play 진행 스냅샷 캐시(dict)
_progress_snap: Dict[str, Any] = {}

# 타임라인 녹색 강조(active_keys) 스냅샷
_active_schedule_keys: Tuple[Tuple[Any, ...], ...] = ()

# FOUP별 집계(pick/place 누적) — foup_index -> FoupCounts
_foup_counts: Dict[int, FoupCounts] = {1: FoupCounts(), 2: FoupCounts(), 3: FoupCounts()}

_toggle_listeners: list = []

# UI 모델(전역 단일) — HUD/본창이 같은 모델을 공유해야 토글이 왕복(깜박임/복귀)하지 않는다.
_ui_models_lock = threading.Lock()
_ui_model_foup: Any = None
_ui_model_device: Any = None
_ui_model_pick: Any = None
_ui_model_syncing: bool = False
_ui_model_hooks_installed: bool = False


def ui_models_are_syncing() -> bool:
    """state -> UI 모델 set_value 동기화 중인지(콜백 재진입 방지용)."""
    return bool(_ui_model_syncing)


def _ensure_ui_models() -> None:
    global _ui_model_foup, _ui_model_device, _ui_model_pick
    if _ui_model_foup is not None and _ui_model_device is not None and _ui_model_pick is not None:
        return
    try:
        from omni.ui import SimpleBoolModel  # type: ignore
    except Exception:
        return
    with _ui_models_lock:
        if _ui_model_foup is None:
            _ui_model_foup = SimpleBoolModel(bool(get_toggle_foup_status()))
        if _ui_model_device is None:
            _ui_model_device = SimpleBoolModel(bool(get_toggle_device_labels()))
        if _ui_model_pick is None:
            _ui_model_pick = SimpleBoolModel(bool(get_toggle_pick_whitelist()))
    _install_ui_model_hooks()


def _read_model_bool(m: Any) -> bool:
    try:
        return bool(m.get_value_as_bool())
    except Exception:
        pass
    try:
        return bool(m.as_bool)
    except Exception:
        pass
    try:
        return bool(m.get_value())
    except Exception:
        return False


def _install_ui_model_hooks() -> None:
    """UI 모델 값 변경(사용자 클릭)을 state로 반영. changed_fn이 안 타는 환경 대응."""
    global _ui_model_hooks_installed
    if _ui_model_hooks_installed:
        return
    if _ui_model_foup is None or _ui_model_device is None or _ui_model_pick is None:
        return

    def _on_any_changed(*_a: Any) -> None:
        # state -> model 동기화(set_value) 중 발생한 이벤트는 무시
        if _ui_model_syncing:
            return
        try:
            f = _read_model_bool(_ui_model_foup)
            d = _read_model_bool(_ui_model_device)
            p = _read_model_bool(_ui_model_pick)
            print(f"[LAM/OverlayUIModel] f={f} d={d} p={p}", flush=True)
            set_toggle_foup_status(f)
            set_toggle_device_labels(d)
            set_toggle_pick_whitelist(p)
        except Exception:
            pass

    for m in (_ui_model_foup, _ui_model_device, _ui_model_pick):
        for hook in ("add_value_changed_fn", "add_item_changed_fn"):
            try:
                fn = getattr(m, hook, None)
                if callable(fn):
                    fn(_on_any_changed)
            except Exception:
                pass
    _ui_model_hooks_installed = True


def get_ui_model_foup_status() -> Any:
    _ensure_ui_models()
    return _ui_model_foup


def get_ui_model_device_labels() -> Any:
    _ensure_ui_models()
    return _ui_model_device


def get_ui_model_pick_whitelist() -> Any:
    _ensure_ui_models()
    return _ui_model_pick


def _sync_ui_model_values() -> None:
    """state -> UI 모델 값 동기화(재귀 방지)."""
    global _ui_model_syncing
    _ensure_ui_models()
    if _ui_model_foup is None or _ui_model_device is None or _ui_model_pick is None:
        return
    if _ui_model_syncing:
        return
    _ui_model_syncing = True
    try:
        try:
            _ui_model_foup.set_value(bool(get_toggle_foup_status()))
        except Exception:
            pass
        try:
            _ui_model_device.set_value(bool(get_toggle_device_labels()))
        except Exception:
            pass
        try:
            _ui_model_pick.set_value(bool(get_toggle_pick_whitelist()))
        except Exception:
            pass
    finally:
        _ui_model_syncing = False


def apply_startup_checkbox_side_effects() -> None:
    """config 기본값 → Viewport 선택 제한 등 런타임 훅 1회 반영."""
    global _startup_checkbox_side_effects_applied
    if _startup_checkbox_side_effects_applied:
        return
    _startup_checkbox_side_effects_applied = True
    try:
        from .lam_viewport_pick_whitelist import disable_pick_whitelist, enable_pick_whitelist

        if get_toggle_pick_whitelist():
            enable_pick_whitelist()
        else:
            disable_pick_whitelist()
    except Exception:
        pass


def register_toggle_listener(fn) -> None:
    """토글 변경 즉시 UI 반영(패널 show/hide)을 위한 리스너."""
    if fn is None:
        return
    with _lock:
        _toggle_listeners.append(fn)


def _notify_toggle_listeners() -> None:
    try:
        with _lock:
            listeners = list(_toggle_listeners)
    except Exception:
        listeners = []
    for fn in listeners:
        try:
            fn()
        except Exception:
            pass


def set_selected_csv_path(path: str) -> None:
    with _lock:
        global _selected_csv_path
        _selected_csv_path = (path or "").strip()


def get_selected_csv_path() -> str:
    with _lock:
        return str(_selected_csv_path)


def set_manual_eq_model(value: str) -> None:
    with _lock:
        global _manual_eq_model
        _manual_eq_model = (value or "").strip()


def get_manual_eq_model() -> str:
    with _lock:
        return str(_manual_eq_model)


def set_last_state_title(title: str) -> None:
    with _lock:
        global _last_state_title
        _last_state_title = str(title or "")


def get_last_state_title() -> str:
    with _lock:
        return str(_last_state_title)


def set_toggle_foup_status(enabled: bool) -> None:
    prev = get_toggle_foup_status()
    import time as _t
    now = float(_t.time())
    # 0.3초 이내 반대값으로 즉시 되돌리는 이벤트는 무시(중복 UI가 싸우는 케이스)
    if bool(enabled) != bool(prev):
        last_ts = float(_last_toggle_change_ts.get("foup", 0.0))
        last_val = bool(_last_toggle_change_val.get("foup", prev))
        if (now - last_ts) < 0.3 and bool(enabled) != bool(last_val):
            print(
                f"[LAM/OverlayState] ignore bounce foup {prev} -> {enabled} (last {last_val} @{now-last_ts:.3f}s)",
                flush=True,
            )
            return
    with _lock:
        global _toggle_foup_status
        _toggle_foup_status = bool(enabled)
    _last_toggle_change_ts["foup"] = now
    _last_toggle_change_val["foup"] = bool(enabled)
    print(f"[LAM/OverlayState] set_toggle_foup_status {prev} -> {bool(enabled)}", flush=True)
    if not bool(enabled):
        # 어떤 경로로든 남아있는 SceneView를 강제 제거(OFF는 반드시 사라져야 함)
        try:
            from .lam_viewport_foup_status_3d import force_remove_all_foup_sceneviews

            force_remove_all_foup_sceneviews()
            print("[LAM/OverlayState] force_remove_all_foup_sceneviews()", flush=True)
        except Exception:
            pass
    _notify_toggle_listeners()
    _sync_ui_model_values()


def get_toggle_foup_status() -> bool:
    with _lock:
        return bool(_toggle_foup_status)


def set_toggle_device_labels(enabled: bool) -> None:
    prev = get_toggle_device_labels()
    import time as _t
    now = float(_t.time())
    if bool(enabled) != bool(prev):
        last_ts = float(_last_toggle_change_ts.get("device", 0.0))
        last_val = bool(_last_toggle_change_val.get("device", prev))
        if (now - last_ts) < 0.3 and bool(enabled) != bool(last_val):
            print(
                f"[LAM/OverlayState] ignore bounce device {prev} -> {enabled} (last {last_val} @{now-last_ts:.3f}s)",
                flush=True,
            )
            return
    with _lock:
        global _toggle_device_labels
        _toggle_device_labels = bool(enabled)
    _last_toggle_change_ts["device"] = now
    _last_toggle_change_val["device"] = bool(enabled)
    print(f"[LAM/OverlayState] set_toggle_device_labels {prev} -> {bool(enabled)}", flush=True)
    if not bool(enabled):
        try:
            from .lam_viewport_device_labels_3d import force_remove_all_device_sceneviews

            force_remove_all_device_sceneviews()
            print("[LAM/OverlayState] force_remove_all_device_sceneviews()", flush=True)
        except Exception:
            pass
    _notify_toggle_listeners()
    _sync_ui_model_values()


def get_toggle_device_labels() -> bool:
    with _lock:
        return bool(_toggle_device_labels)


def set_toggle_pick_whitelist(enabled: bool) -> None:
    with _lock:
        global _toggle_pick_whitelist
        _toggle_pick_whitelist = bool(enabled)
    # 토글 변경 시 즉시 적용
    try:
        from .lam_viewport_pick_whitelist import enable_pick_whitelist, disable_pick_whitelist

        if bool(enabled):
            enable_pick_whitelist()
        else:
            disable_pick_whitelist()
    except Exception:
        pass
    _notify_toggle_listeners()
    _sync_ui_model_values()


def get_toggle_pick_whitelist() -> bool:
    with _lock:
        return bool(_toggle_pick_whitelist)


def update_progress_snap(snap: Dict[str, Any]) -> None:
    with _lock:
        global _progress_snap
        _progress_snap = dict(snap or {})


def get_progress_snap() -> Dict[str, Any]:
    with _lock:
        return dict(_progress_snap)


def update_active_schedule_keys(keys: Any) -> None:
    # keys is a frozenset of tuples (match keys)
    try:
        tup = tuple(sorted(tuple(k) for k in (keys or ())))
    except Exception:
        tup = ()
    with _lock:
        global _active_schedule_keys
        _active_schedule_keys = tup


def get_active_schedule_keys() -> Tuple[Tuple[Any, ...], ...]:
    with _lock:
        return tuple(_active_schedule_keys)


def set_foup_counts(foup_index: int, picked: int, placed_back: int, *, total: int = 25) -> None:
    fi = int(foup_index)
    if fi not in (1, 2, 3):
        return
    with _lock:
        _foup_counts[fi] = FoupCounts(total=int(total), picked_count=int(picked), placed_back_count=int(placed_back))


def get_foup_counts(foup_index: int) -> FoupCounts:
    fi = int(foup_index)
    with _lock:
        return _foup_counts.get(fi, FoupCounts())


def get_snapshot() -> Dict[str, Any]:
    with _lock:
        return {
            "selected_csv_path": str(_selected_csv_path),
            "manual_eq_model": str(_manual_eq_model),
            "toggle_foup_status": bool(_toggle_foup_status),
            "toggle_device_labels": bool(_toggle_device_labels),
            "toggle_pick_whitelist": bool(_toggle_pick_whitelist),
            "progress": dict(_progress_snap),
            "active_schedule_keys": tuple(_active_schedule_keys),
            "foup_counts": {k: v for k, v in _foup_counts.items()},
        }


__all__ = [
    "FoupCounts",
    "set_selected_csv_path",
    "get_selected_csv_path",
    "set_manual_eq_model",
    "get_manual_eq_model",
    "set_toggle_foup_status",
    "get_toggle_foup_status",
    "set_toggle_device_labels",
    "get_toggle_device_labels",
    "apply_startup_checkbox_side_effects",
    "set_toggle_pick_whitelist",
    "get_toggle_pick_whitelist",
    "register_toggle_listener",
    "get_ui_model_foup_status",
    "get_ui_model_device_labels",
    "get_ui_model_pick_whitelist",
    "ui_models_are_syncing",
    "update_progress_snap",
    "get_progress_snap",
    "update_active_schedule_keys",
    "get_active_schedule_keys",
    "set_foup_counts",
    "get_foup_counts",
    "get_snapshot",
]
