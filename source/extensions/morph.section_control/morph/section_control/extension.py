# ---------------------------------------------------------------------
# extension.py  (외부에서 service를 가져와 호출할 수 있도록 싱글톤 제공)
# ---------------------------------------------------------------------
import omni.ext
import omni.kit.app

from .service import SectionControlService
from .ui_dummy import DummySectionControlUI

# ✅ 외부 호출용 전역 핸들 (다른 익스텐션/스크립트에서 import 가능)
_SERVICE_SINGLETON = None


def get_service() -> SectionControlService:
    """
    다른 익스텐션/스크립트에서:
        from morph.section_control.extension import get_service
        svc = get_service()
    형태로 호출한다.
    """
    return _SERVICE_SINGLETON


class MyCompanySectionControlExtension(omni.ext.IExt):
    def on_startup(self, ext_id):
        global _SERVICE_SINGLETON

        self._ext_id = ext_id

        self._service = SectionControlService()
        self._service.startup()

        # ✅ singleton 공개
        _SERVICE_SINGLETON = self._service

        # (선택) 로컬 디버그 UI
        self._ui = DummySectionControlUI(self._service)

        # 시작 시 window 한 번만 보이게
        self._show_once_sub = None
        try:
            app = omni.kit.app.get_app()
            stream = app.get_post_update_event_stream()

            def _show_once(e):
                if self._ui and self._ui.window:
                    self._ui.window.visible = True
                    self._ui.window.focus()
                if self._show_once_sub:
                    self._show_once_sub.unsubscribe()
                    self._show_once_sub = None

            self._show_once_sub = stream.create_subscription_to_pop(
                _show_once,
                name="section_control_show_window_once",
            )
        except Exception:
            try:
                self._ui.window.visible = True
            except Exception:
                pass

    def on_shutdown(self):
        global _SERVICE_SINGLETON

        try:
            if self._show_once_sub:
                self._show_once_sub.unsubscribe()
        except Exception:
            pass
        self._show_once_sub = None

        try:
            if self._ui:
                self._ui.destroy()
        except Exception:
            pass
        self._ui = None

        try:
            if self._service:
                self._service.shutdown()
        except Exception:
            pass
        self._service = None

        # ✅ singleton 해제
        _SERVICE_SINGLETON = None
