import omni.ext
from .core import initialize_service, shutdown_service

class ManualNavExtension(omni.ext.IExt):
    def on_startup(self, ext_id):
        initialize_service()
        from .ui_dummy import create_ui   
        create_ui()                 

    def on_shutdown(self):
        shutdown_service()