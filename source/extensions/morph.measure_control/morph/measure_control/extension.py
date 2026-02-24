import omni.ext
import omni.kit.app

from .service import MeasureControlService
from .ui_dummy import DummyMeasureControlUI

_SERVICE_SINGLETON = None


def get_service() -> MeasureControlService:
    return _SERVICE_SINGLETON


class MyCompanyMeasureControlExtension(omni.ext.IExt):
    def on_startup(self, ext_id):
        global _SERVICE_SINGLETON

        self._ext_id = ext_id

        self._service = MeasureControlService()
        self._service.startup()

        _SERVICE_SINGLETON = self._service

        self._ui = DummyMeasureControlUI(self._service)

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
                name="measure_control_show_window_once",
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

        _SERVICE_SINGLETON = None
