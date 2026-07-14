from .extension_handlers import HANDLERS

import omni.ext


class Extension(omni.ext.IExt):
    def on_startup(self, ext_id: str):
        self._handlers = [handler_class() for handler_class in HANDLERS]
        try:
            import carb

            carb.log_info(
                f"[sk.hyview_messaging] started ext_id={ext_id} "
                f"handlers={[h.__class__.__name__ for h in self._handlers]}"
            )
        except Exception:
            pass

    def on_shutdown(self):
        for handler in getattr(self, "_handlers", []):
            try:
                handler.on_shutdown()
            except Exception:
                pass
        self._handlers = []
