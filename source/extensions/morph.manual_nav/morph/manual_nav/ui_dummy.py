import omni.ui as ui
from .service import get_service

class ManualNavDummyUI:
    def __init__(self):
        self._svc = get_service()
        if not self._svc:
            print("ManualNavService not ready - UI skipped")
            return

        self._window = ui.Window("Manual Nav Controls", width=240, height=140, visible=False)
        
        with self._window.frame:
            with ui.VStack(spacing=10, height=0):
                ui.Label("Teleport Control", alignment=ui.Alignment.CENTER, height=20)
                ui.Button("Teleport ON", clicked_fn=self._on, height=40)
                ui.Button("Teleport OFF", clicked_fn=self._off, height=40)

        # 강제 표시 + 재빌드 (startup 타이밍 우회)
        self._window.visible = True
        self._window.frame.rebuild()

    def _on(self):
        self._svc.teleport_on()

    def _off(self):
        self._svc.teleport_off()

_ui_instance = None

def create_ui():
    global _ui_instance
    if _ui_instance is None:
        _ui_instance = ManualNavDummyUI()