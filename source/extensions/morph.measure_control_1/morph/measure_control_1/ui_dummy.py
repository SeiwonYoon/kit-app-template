import omni.ui as ui
import carb


class DummyMeasureControlUI:
    _TARGET_PRIM_PATH = "/World/Cube"

    def __init__(self, service):
        self._service = service
        self._m_status = ui.SimpleStringModel("Ready")

        self._window = ui.Window("Measure Control 11 (Dummy UI)", width=520, height=140, visible=False)
        self._build()

    @property
    def window(self):
        return self._window

    def destroy(self):
        self._window = None
        self._service = None

    @staticmethod
    def _ro_string(model: ui.SimpleStringModel, width=0):
        try:
            return ui.StringField(model=model, read_only=True, width=width)
        except TypeError:
            field = ui.StringField(model=model, width=width)
            try:
                field.enabled = False
            except Exception:
                pass
            return field

    def _on_run_mesh_clicked(self):
        if not self._service:
            self._m_status.set_value("service is not available")
            return

        try:
            self._m_status.set_value(f"running mesh measure for {self._TARGET_PRIM_PATH} ...")
            result = self._service.measure_mesh_for_prim_path(self._TARGET_PRIM_PATH)
            msg = str(result.get("message", "unknown result"))
            self._m_status.set_value(msg)
            carb.log_info(f"[measure_control.ui] {msg}")
        except Exception as ex:
            self._m_status.set_value(f"unexpected error: {ex}")
            carb.log_error(f"[measure_control.ui] unexpected error: {ex}")

    def _click_button_0(self):
        print("click button 0")
        prim = self._ctx.get_stage().GetPrimAtPath("/World/Cube")
        print(prim)

    def _click_button_1(self):
        print("click button 1")

    def _build(self):
        with self._window.frame:
            with ui.VStack(spacing=8, height=0):
                ui.Label(f"Target prim: {self._TARGET_PRIM_PATH}", height=24)
                with ui.HStack(height=24):
                    ui.Button("Button_0", height=28, clicked_fn=self._click_button_0)
                    ui.Button("Button_1", height=28, clicked_fn=self._click_button_1)
                    ui.Label("Status", width=50)
