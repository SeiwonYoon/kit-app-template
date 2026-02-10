import omni.ext
import omni.ui as ui
import omni.usd
import omni.client
import os
import asyncio

class UsdLoader(omni.ext.IExt):
    def on_startup(self, ext_id):
        self._window = ui.Window("USD Loader", width=450, height=130)
        with self._window.frame:
            with ui.VStack(padding=10, spacing=10):
                self.fld = ui.StringField()
                self.lbl = ui.Label("", style={"color": 0xFFFF0000})
                
                ui.Button("Load", clicked_fn=lambda: asyncio.ensure_future(self._validate_and_load()))

        asyncio.ensure_future(self._load_on_launch())

    async def _load_on_launch(self):
        await asyncio.sleep(2.0) 
        
        # [수정] 창이 혹시라도 숨겨져 있다면 다시 보이게 설정
        if self._window:
            self._window.visible = True
            self._window.focus()

    async def _validate_and_load(self):
        path = self.fld.model.get_value_as_string().strip()
        self.lbl.text = ""

        if not path or not path.lower().endswith(('.usd', '.usda', '.usdc')):
            self.lbl.text = "Error: Invalid URL or File extension."; return

        try:
            result, _ = await asyncio.wait_for(omni.client.stat_async(path), timeout=1.5)
            
            if result != omni.client.Result.OK:
                self.lbl.text = "Error: This URL does not exist."; return
        except Exception:
            self.lbl.text = "Error: Connection timeout (Wrong Domain)."; return

        print(f"[Success] Valid path found. Loading...")
        omni.usd.get_context().open_stage(path)

    def on_shutdown(self):
        if self._window: self._window.destroy()