from .extension_handlers import HANDLERS
from .stage_loading import LoadingManager
from .stage_management import StageManager

import omni.ext


class Extension(omni.ext.IExt):
    def on_startup(self, ext_id: str):
        self._loading_manager: LoadingManager = LoadingManager()
        self._stage_manager: StageManager = StageManager()
        self._handlers = [handler_class() for handler_class in HANDLERS]
        try:
            import carb

            carb.log_info(
                f"[morph.hyview_messaging] started ext_id={ext_id} handlers={[h.__class__.__name__ for h in self._handlers]}"
            )
        except Exception:
            pass

    def on_shutdown(self):
        if self._loading_manager:
            self._loading_manager.on_shutdown()
            self._loading_manager = None
        if self._stage_manager:
            self._stage_manager.on_shutdown()
            self._stage_manager = None
        for handler in getattr(self, "_handlers", []):
            try:
                handler.on_shutdown()
            except Exception:
                pass
        self._handlers = []
