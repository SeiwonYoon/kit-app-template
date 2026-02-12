import omni.ext
import omni.ui as ui
import asyncio
from omni.kit.viewport.utility import create_viewport_window, get_viewport_from_window_name

class MultiViewportExtension(omni.ext.IExt):
    def on_startup(self, ext_id):
        self._vp_windows = {}
        self._on_off_btns = {}
        self._title_btns = {} 
        self._fix_btns = {}    
        
        # 5개 시점 구성
        self._camera_configs = {
            "Top": "/OmniverseKit_Top",
            "Front": "/OmniverseKit_Front",
            "Right": "/OmniverseKit_Right",
            "Persp 1": "/OmniverseKit_Persp_01",
            "Persp 2": "/OmniverseKit_Persp_02"
        }
        
        self._control_window = ui.Window("Viewport Manager", width=300, height=220)
        with self._control_window.frame:
            with ui.VStack(spacing=8, padding=10):
                for label_name in self._camera_configs.keys():
                    with ui.HStack(height=30, spacing=5):
                        ui.Label(label_name, width=60)
                        
                        # ON <-> OFF 토글 버튼
                        o_btn = ui.Button("OFF", width=60)
                        o_btn.set_clicked_fn(lambda n=label_name: self._toggle_viewport(n))
                        self._on_off_btns[label_name] = o_btn
                        
                        # Title <-> Hidden 토글 버튼
                        t_btn = ui.Button("Title", width=70)
                        t_btn.set_clicked_fn(lambda n=label_name: self._toggle_title_bar(n))
                        self._title_btns[label_name] = t_btn
                        
                        # Fix <-> Unlock 토글 버튼
                        f_btn = ui.Button("Fix", width=70)
                        f_btn.set_clicked_fn(lambda n=label_name: self._toggle_fix_window(n))
                        self._fix_btns[label_name] = f_btn

    def _toggle_viewport(self, name):
        btn = self._on_off_btns[name]
        
        if name in self._vp_windows and self._vp_windows[name]:
            self._close_viewport(name)
            btn.text = "OFF"
            btn.style = {} # 스타일 초기화
            return

        if len(self._vp_windows) >= 5:
            return

        new_vp_win = create_viewport_window(name)
        if new_vp_win:
            self._vp_windows[name] = new_vp_win
            btn.text = "ON"
            btn.style = {"background_color": 0xFF888888} # ON 상태 표시
            asyncio.ensure_future(self._apply_camera_settings(name))

    def _toggle_title_bar(self, name):
        if name in self._vp_windows and self._vp_windows[name]:
            window = self._vp_windows[name]
            btn = self._title_btns[name]
            
            if window.flags & ui.WINDOW_FLAGS_NO_TITLE_BAR:
                window.flags &= ~ui.WINDOW_FLAGS_NO_TITLE_BAR
                btn.text = "Title"
                btn.style = {"background_color": 0xFF666666} 
            else:
                window.flags |= ui.WINDOW_FLAGS_NO_TITLE_BAR
                btn.text = "Hidden"
                btn.style = {"background_color": 0xFF44AA44} 
            
            # 레이아웃 갱신
            window.visible = False
            window.visible = True

    def _toggle_fix_window(self, name):
        if name in self._vp_windows and self._vp_windows[name]:
            window = self._vp_windows[name]
            btn = self._fix_btns[name]
            
            if window.flags & ui.WINDOW_FLAGS_NO_MOVE:
                window.flags &= ~ui.WINDOW_FLAGS_NO_MOVE
                btn.text = "Fix"
                btn.style = {"background_color": 0xFF666666}
            else:
                window.flags |= ui.WINDOW_FLAGS_NO_MOVE
                btn.text = "Unlock"
                btn.style = {"background_color": 0xFF2266AA} 
                
    async def _apply_camera_settings(self, name):
        viewport_api = get_viewport_from_window_name(name)
        if viewport_api:
            target_cam = self._camera_configs.get(name)
            try:
                viewport_api.camera_path = target_cam
                await viewport_api.wait_for_render_settings_change()
            except:
                pass

    def _close_viewport(self, name):
        if name in self._vp_windows and self._vp_windows[name]:
            self._vp_windows[name].destroy()
            del self._vp_windows[name]
            
            # 모든 버튼 상태 리셋
            self._on_off_btns[name].text = "OFF"
            self._on_off_btns[name].style = {}
            self._title_btns[name].text = "Title"
            self._title_btns[name].style = {}
            self._fix_btns[name].text = "Fix"
            self._fix_btns[name].style = {}

    def on_shutdown(self):
        for name in list(self._vp_windows.keys()):
            self._close_viewport(name)
        if self._control_window:
            self._control_window.destroy()