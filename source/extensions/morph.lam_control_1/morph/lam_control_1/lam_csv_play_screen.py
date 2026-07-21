"""CSV 시뮬 재생 — 화면별 registry/scheduler·재생 세션·뷰포트 조회."""

from __future__ import annotations

import contextvars
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

_csv_play_screen_ctx: contextvars.ContextVar[int] = contextvars.ContextVar(
    "lam_csv_play_screen",
    default=1,
)

_sessions_lock = threading.Lock()
_sessions: Dict[int, "CsvPlayScreenSession"] = {}


def current_csv_play_screen() -> int:
    return max(1, int(_csv_play_screen_ctx.get()))


def csv_play_screen_for_usd_context(usd_context_name: Optional[str]) -> int:
    """애니메이션 USD context → CSV Play 화면 번호 (default/빈 context = 1)."""
    cn = str(usd_context_name or "").strip()
    if not cn:
        return 1
    prefix = "morph_lam_split_aux_"
    if cn.startswith(prefix):
        try:
            tile_index = int(cn[len(prefix) :])
            return max(2, tile_index + 1)
        except ValueError:
            pass
    return 1


@dataclass
class CsvPlayScreenSession:
    """화면 1개분 CSV Play 런타임 상태 (stop·pause·진행·타임라인 강조)."""

    screen: int
    stop_event: threading.Event = field(default_factory=threading.Event)
    material_test_stop: threading.Event = field(default_factory=threading.Event)
    runner_lock: threading.Lock = field(default_factory=threading.Lock)
    active_runners: List[Any] = field(default_factory=list)
    pause_checkpoint: Any = None
    pause_armed: bool = False
    session_active: bool = False
    live_speed_lock: threading.Lock = field(default_factory=threading.Lock)
    live_speed_scale: float = 1.0
    time_base_wall: float = 0.0
    csv_time_offset: float = 0.0
    wall_elapsed_offset: float = 0.0
    live_speed_ui_reader: Optional[Callable[[], float]] = None
    progress_ui_cb: Optional[Callable[[float, float, float, float], None]] = None
    timeline_highlight_cb: Optional[Callable[[frozenset], None]] = None
    timeline_active_keys_lock: threading.Lock = field(default_factory=threading.Lock)
    timeline_active_keys: set = field(default_factory=set)
    progress_snap_lock: threading.Lock = field(default_factory=threading.Lock)
    progress_snap: Dict[str, Any] = field(
        default_factory=lambda: {
            "process_only": False,
            "json_done": 0,
            "json_total": 0,
            "csv_t_display": 0.0,
            "csv_time_offset": 0.0,
            "wall_elapsed_display": 0.0,
            "csv_total": 0.0,
            "t0": 0.0,
            "speed_scale": 1.0,
        }
    )
    process_only_playhead_lock: threading.Lock = field(default_factory=threading.Lock)
    process_only_playhead_csv: float = 0.0
    process_only_playhead_wall: float = 0.0
    process_only_started_keys: set = field(default_factory=set)
    global_end_lock: threading.Lock = field(default_factory=threading.Lock)
    global_wall_end: float = 0.0
    global_csv_end: float = 0.0
    # Play 진행률 ticker 중지 — 화면별로 분리 (전역이면 화면1 stop 이 화면2 ticker 도 끊음)
    progress_stop: threading.Event = field(default_factory=threading.Event)
    # begin/end timekeeping 경합 방지 — 구 play finally 가 신 play session_active 를 끄지 않게
    play_epoch: int = 0
    # 블록/레인 JSON worker — 공정만보기 전환·정지 시 메인 스레드 외 잔존 join 용
    child_workers_lock: threading.Lock = field(default_factory=threading.Lock)
    child_workers: List[threading.Thread] = field(default_factory=list)


def csv_play_screen_session(screen: Optional[int] = None) -> CsvPlayScreenSession:
    si = max(1, int(screen if screen is not None else current_csv_play_screen()))
    with _sessions_lock:
        sess = _sessions.get(si)
        if sess is None:
            sess = CsvPlayScreenSession(screen=si)
            _sessions[si] = sess
        return sess


class csv_play_screen_binding:
    """재생 worker 스레드에서 ``current_csv_play_screen()`` 이 올바른 화면을 가리키게 한다."""

    def __init__(self, screen: int) -> None:
        self._screen = max(1, int(screen))
        self._token: Optional[contextvars.Token] = None

    def __enter__(self) -> CsvPlayScreenSession:
        self._token = _csv_play_screen_ctx.set(self._screen)
        return csv_play_screen_session(self._screen)

    def __exit__(self, *_a: Any) -> None:
        if self._token is not None:
            _csv_play_screen_ctx.reset(self._token)
            self._token = None


def get_registry_scheduler_for_lam_screen(
    lam_window: Any,
    screen: int,
    *,
    allow_fallback: bool = True,
) -> Tuple[Any, Any]:
    """화면별 AnimationInstanceRegistry / PlaybackScheduler.

    화면2+ 는 ``SplitScreenRuntime`` 전용. ``allow_fallback=False`` 이면 없을 때
    화면1 registry 로 조용히 넘어가지 않는다 (TBS per-screen 패턴).
    """
    si = max(1, int(screen))
    if si <= 1:
        return getattr(lam_window, "_registry", None), getattr(lam_window, "_scheduler", None)
    ext = getattr(lam_window, "_kit_ext", None)
    if ext is not None:
        try:
            from .lam_split_composed_loader import get_split_runtime_for_screen

            rt = get_split_runtime_for_screen(ext, si)
            if rt is not None:
                return rt.registry, rt.scheduler
        except Exception:
            pass
    if allow_fallback:
        return getattr(lam_window, "_registry", None), getattr(lam_window, "_scheduler", None)
    return None, None


def usd_context_name_for_screen(ext: Any, screen: int) -> Optional[str]:
    try:
        from .lam_multi_viewport_diag import usd_context_for_screen

        return usd_context_for_screen(ext, int(screen))
    except Exception:
        return None


def get_stage_for_screen(ext: Any, screen: int) -> Any:
    """화면 USD context 의 Stage (없으면 default context)."""
    si = max(1, int(screen))
    if si <= 1:
        try:
            from .lam_prim_utils import get_stage

            return get_stage()
        except Exception:
            return None
    ctx_name = usd_context_name_for_screen(ext, si)
    if not ctx_name:
        try:
            from .lam_prim_utils import get_stage

            return get_stage()
        except Exception:
            return None
    try:
        import omni.usd as ou

        ctx = ou.get_context(str(ctx_name))
        return ctx.get_stage() if ctx is not None else None
    except Exception:
        try:
            from .lam_prim_utils import get_stage

            return get_stage()
        except Exception:
            return None


def resolve_viewport_window_for_screen(
    ext: Any,
    screen: int,
    *,
    main_viewport: Any = None,
) -> Optional[Any]:
    """분할 타일 ``ViewportWindow`` / HUD mount (``get_frame`` + ``viewport_api``).

    화면2+: 해당 Dock 창(``LAM_SimSplit_*``) 또는 Widget hud_mount 만.
    **active/main Viewport 로 fallback 하지 않음** (화면1 라벨 깜빡임·오염 방지).
    """
    si = max(1, int(screen))
    # Widget 분할: 타일별 hud_mount 가 SceneView 부착 지점 (화면1·2 공통).
    if ext is not None:
        try:
            from .lam_multi_viewport import _viewport_window_name_for_screen
            from .lam_multi_viewport_widget import (
                get_split_hud_mount,
                is_split_widget_layout_active,
            )

            if is_split_widget_layout_active(ext):
                wname = "Viewport" if si <= 1 else _viewport_window_name_for_screen(si)
                mount = get_split_hud_mount(ext, wname)
                if mount is not None and callable(getattr(mount, "get_frame", None)):
                    return mount
        except Exception:
            pass
    if si <= 1:
        if main_viewport is not None:
            try:
                dedicated = getattr(main_viewport, "_dedicated_window", None)
                if dedicated is not None and callable(getattr(dedicated, "get_frame", None)):
                    return dedicated
            except Exception:
                pass
    try:
        from .lam_multi_viewport import (
            _resolve_viewport_window_for_workspace_name,
            _viewport_window_name_for_screen,
        )

        wname = "Viewport" if si <= 1 else _viewport_window_name_for_screen(si)
        vw = _resolve_viewport_window_for_workspace_name(wname)
        if vw is not None and callable(getattr(vw, "get_frame", None)):
            return vw
        # Dock: create_viewport_window 결과가 get_frame 없이 API 만인 경우도 반환
        # (overlay 는 viewport_api / frame 폴백, play 는 API 사용)
        if si > 1 and vw is not None:
            if getattr(vw, "viewport_api", None) is not None:
                return vw
            if hasattr(vw, "usd_context_name") or hasattr(vw, "set_usd_context_name"):
                return vw
    except Exception:
        pass
    if si <= 1 and main_viewport is not None:
        try:
            dedicated = getattr(main_viewport, "_dedicated_window", None)
            if dedicated is not None and callable(getattr(dedicated, "get_frame", None)):
                return dedicated
        except Exception:
            pass
    # 화면1 만 active Viewport fallback. 화면2+ 는 절대 화면1 로 떨어지지 않음.
    if si <= 1:
        try:
            from omni.kit.viewport.utility import get_active_viewport_window  # type: ignore

            win = get_active_viewport_window()
            if win is not None and callable(getattr(win, "get_frame", None)):
                return win
        except Exception:
            pass
    return None


def resolve_viewport_api_for_screen(ext: Any, screen: int) -> Optional[Any]:
    """화면별 viewport_api — play/camera. 화면2+ 는 ``LAM_SimSplit_*`` 만."""
    si = max(1, int(screen))
    if ext is not None:
        try:
            from .lam_multi_viewport import _viewport_window_name_for_screen
            from .lam_multi_viewport_widget import (
                get_split_viewport_api,
                is_split_widget_layout_active,
            )

            wname = "Viewport" if si <= 1 else _viewport_window_name_for_screen(si)
            if is_split_widget_layout_active(ext):
                api = get_split_viewport_api(ext, wname)
                if api is not None:
                    return api
        except Exception:
            pass
    try:
        from .lam_multi_viewport import (
            _resolve_viewport_api_for_workspace_name,
            _viewport_window_name_for_screen,
        )

        wname = "Viewport" if si <= 1 else _viewport_window_name_for_screen(si)
        api = _resolve_viewport_api_for_workspace_name(wname)
        if api is not None:
            return api
    except Exception:
        pass
    if si <= 1:
        try:
            from omni.kit.viewport.utility import get_active_viewport  # type: ignore

            return get_active_viewport()
        except Exception:
            return None
    return None


__all__ = [
    "CsvPlayScreenSession",
    "csv_play_screen_binding",
    "csv_play_screen_session",
    "csv_play_screen_for_usd_context",
    "current_csv_play_screen",
    "get_registry_scheduler_for_lam_screen",
    "get_stage_for_screen",
    "resolve_viewport_api_for_screen",
    "resolve_viewport_window_for_screen",
    "usd_context_name_for_screen",
]
