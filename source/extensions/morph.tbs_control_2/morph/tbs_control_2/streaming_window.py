"""스트리밍 Kit — livestream.app 이 있을 때만 뷰포트 polish (창 리사이즈 가드 없음)."""

from __future__ import annotations

from typing import Any

import carb.settings
import omni.kit.app as kit_app


def _is_streaming_deployment() -> bool:
    try:
        settings = carb.settings.get_settings()
        if settings and bool(settings.get("/app/morph/streamingUi")):
            return True
    except Exception:
        pass
    try:
        em = kit_app.get_app().get_extension_manager()
        if em is not None and em.is_extension_enabled("omni.kit.livestream.app"):
            return True
    except Exception:
        pass
    return False


def _on_app_window_resize(ext: Any, _event: Any) -> None:
    try:
        from . import sim_multi_view as smv
        from .kit_chrome_visibility import apply_viewport_dock_tab_bars_hidden

        sn = int(getattr(ext, "_sim_viewport_split_count", 1) or 1)
        smv.set_viewport_fill_frame_for_split_count(sn, True)
        apply_viewport_dock_tab_bars_hidden()
        if sn >= 2:
            smv.apply_viewport_split_tab_chrome(sn)
    except Exception:
        pass


def install_streaming_window_resize_hooks(ext: Any) -> None:
    if not _is_streaming_deployment():
        return
    sub = getattr(ext, "_streaming_resize_sub", None)
    if sub is not None:
        return
    try:
        import omni.appwindow

        factory = omni.appwindow.acquire_app_window_factory_interface()
        aw = factory.get_default_app_window()
        ext._streaming_resize_sub = aw.get_window_resize_event_stream().create_subscription_to_pop(
            lambda e, _ext=ext: _on_app_window_resize(_ext, e),
            name="morph.tbs_control_2:streaming_window_resize",
        )
    except Exception:
        ext._streaming_resize_sub = None


def teardown_streaming_window_hooks(ext: Any) -> None:
    sub = getattr(ext, "_streaming_resize_sub", None)
    if sub is not None:
        try:
            sub.unsubscribe()
        except Exception:
            pass
    ext._streaming_resize_sub = None
