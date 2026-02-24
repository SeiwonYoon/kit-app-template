import omni.ext
import omni.ui as ui
import asyncio
import omni.usd
from omni.kit.viewport.utility import create_viewport_window, get_viewport_from_window_name
from pxr import Usd, UsdGeom, Gf

class SmartNineViewportExtension(omni.ext.IExt):
    def on_startup(self, ext_id):
        self._vp_windows = {}
        dis, h = 500.0, 300.0
        
        # 카메라 구성 (중앙은 기본 Perspective 유지)
        self._camera_configs = {
            "NW":    {"path": "/World/Cameras/Cam_NW", "pos": Gf.Vec3d(-dis, dis, h), "rot": Gf.Vec3d(60, 0, -135), "grid": (0, 0)},
            "North": {"path": "/World/Cameras/Cam_N",  "pos": Gf.Vec3d(0, dis, h),    "rot": Gf.Vec3d(60, 0, 180),  "grid": (1, 0)},
            "NE":    {"path": "/World/Cameras/Cam_NE", "pos": Gf.Vec3d(dis, dis, h),  "rot": Gf.Vec3d(60, 0, 135),  "grid": (2, 0)},
            "West":  {"path": "/World/Cameras/Cam_W",  "pos": Gf.Vec3d(-dis, 0, h),   "rot": Gf.Vec3d(60, 0, -90),  "grid": (0, 1)},
            "MAIN":  {"path": "/OmniverseKit_Persp",   "pos": None,                  "rot": None,                  "grid": (1, 1)},
            "East":  {"path": "/World/Cameras/Cam_E",  "pos": Gf.Vec3d(dis, 0, h),    "rot": Gf.Vec3d(60, 0, 90),   "grid": (2, 1)},
            "SW":    {"path": "/World/Cameras/Cam_SW", "pos": Gf.Vec3d(-dis, -dis, h), "rot": Gf.Vec3d(60, 0, -45),  "grid": (0, 2)},
            "South": {"path": "/World/Cameras/Cam_S",  "pos": Gf.Vec3d(0, -dis, h),   "rot": Gf.Vec3d(60, 0, 0),    "grid": (1, 2)},
            "SE":    {"path": "/World/Cameras/Cam_SE", "pos": Gf.Vec3d(dis, -dis, h), "rot": Gf.Vec3d(60, 0, 45),   "grid": (2, 2)},
        }

        self._control_window = ui.Window("Viewport Manager", width=250, height=100)
        with self._control_window.frame:
            with ui.VStack(spacing=10, padding=10):
                # 단일 통합 버튼
                self._main_btn = ui.Button("Active MultiView", clicked_fn=self._handle_main_button, height=50)

    def _handle_main_button(self):
        """버튼 하나로 모든 상태(생성, 복구, 종료)를 관리"""
        # 1. 아예 생성이 안 된 상태 -> 최초 생성
        if not self._vp_windows:
            self._toggle_3x3_grid(True)
            self._main_btn.text = "CLOSE DASHBOARD"
            return

        # 2. 생성은 됐는데 숨겨진 상태 (포커스 모드 중) -> 대시보드 복구
        is_any_hidden = any(not ui.Workspace.get_window(name).visible for name in self._vp_windows if ui.Workspace.get_window(name))
        if is_any_hidden:
            self._restore_dashboard()
            self._main_btn.text = "CLOSE DASHBOARD"
            return

        # 3. 켜져 있는 상태에서 누르면 -> 완전히 끄기
        self._toggle_3x3_grid(False)
        self._main_btn.text = "Active MultiView"

    def _toggle_3x3_grid(self, turn_on):
        default_vp = ui.Workspace.get_window("Viewport")
        if default_vp:
            default_vp.visible = not turn_on

        if turn_on:
            for name in self._camera_configs.keys():
                self._create_individual_viewport(name)
            asyncio.ensure_future(self._setup_3x3_layout())
        else:
            for name in list(self._vp_windows.keys()):
                self._close_viewport(name)

    def _create_individual_viewport(self, name):
        config = self._camera_configs[name]
        cam_path = config["path"]
        
        # 카메라 생성 (MAIN 제외)
        if name != "MAIN":
            stage = omni.usd.get_context().get_stage()
            if not stage.GetPrimAtPath(cam_path):
                cam_prim = UsdGeom.Camera.Define(stage, cam_path)
                xformable = UsdGeom.Xformable(cam_prim)
                xformable.AddTranslateOp(UsdGeom.XformOp.PrecisionDouble).Set(config["pos"])
                xformable.AddRotateXYZOp(UsdGeom.XformOp.PrecisionDouble).Set(config["rot"])

        new_vp_win = create_viewport_window(name)
        if new_vp_win:
            self._vp_windows[name] = new_vp_win
            window = ui.Workspace.get_window(name)
            if window:
                window.flags = (ui.WINDOW_FLAGS_NO_TITLE_BAR | ui.WINDOW_FLAGS_NO_MOVE | 
                                ui.WINDOW_FLAGS_NO_RESIZE | ui.WINDOW_FLAGS_NO_SCROLLBAR)
                if hasattr(window.frame, "set_mouse_double_clicked_fn"):
                    window.frame.set_mouse_double_clicked_fn(lambda x, y, b, m, n=name: self._focus_viewport(n))
            
            asyncio.ensure_future(self._apply_camera_settings(name, cam_path))

    def _focus_viewport(self, name):
        """더블 클릭 시 그리드 숨기고 메인 뷰포트 확대"""
        for win_name in self._vp_windows:
            win = ui.Workspace.get_window(win_name)
            if win: win.visible = False
        
        default_vp_win = ui.Workspace.get_window("Viewport")
        if default_vp_win:
            default_vp_win.visible = True
            vp_api = get_viewport_from_window_name("Viewport")
            if vp_api: vp_api.camera_path = self._camera_configs[name]["path"]
        
        # 포커스 상태가 되면 버튼 텍스트를 복구 모드로 변경
        self._main_btn.text = "Active MultiView"

    def _restore_dashboard(self):
        """메인 뷰 숨기고 9분할 다시 표시"""
        default_vp_win = ui.Workspace.get_window("Viewport")
        if default_vp_win: default_vp_win.visible = False
        for win_name in self._vp_windows:
            win = ui.Workspace.get_window(win_name)
            if win: win.visible = True

    async def _setup_3x3_layout(self):
        await asyncio.sleep(0.5)
        main_w = ui.Workspace.get_main_window_width()
        main_h = ui.Workspace.get_main_window_height()
        win_w, win_h = main_w / 3, main_h / 3
        
        for name, config in self._camera_configs.items():
            window = ui.Workspace.get_window(name)
            if window:
                col, row = config["grid"]
                window.position_x, window.position_y = col * win_w, row * win_h
                window.width, window.height = win_w, win_h

    async def _apply_camera_settings(self, name, cam_path):
        for _ in range(20):
            api = get_viewport_from_window_name(name)
            if api:
                api.camera_path = cam_path
                return
            await asyncio.sleep(0.1)

    def _close_viewport(self, name):
        if name in self._vp_windows:
            self._vp_windows[name].destroy()
            del self._vp_windows[name]

    def on_shutdown(self):
        for name in list(self._vp_windows.keys()): self._close_viewport(name)