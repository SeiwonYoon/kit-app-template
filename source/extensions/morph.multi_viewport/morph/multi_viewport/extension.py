import omni.ext
import omni.ui as ui
import asyncio
import omni.usd
from omni.kit.viewport.utility import create_viewport_window, get_viewport_from_window_name
from pxr import Usd, UsdGeom, Gf

class NineViewportExtension(omni.ext.IExt):
    def on_startup(self, ext_id):
        self._vp_windows = {}
        dis, h = 500.0, 300.0
        
        # 3x3 그리드 구성 (Col, Row)
        # 중앙(1,1)은 기본 Persp 카메라 사용
        self._camera_configs = {
            "NW":    {"path": "/World/Cameras/Cam_NW", "pos": Gf.Vec3d(-dis, dis, h), "rot": Gf.Vec3d(60, 0, -135), "grid": (0, 0)},
            "North": {"path": "/World/Cameras/Cam_N",  "pos": Gf.Vec3d(0, dis, h),    "rot": Gf.Vec3d(60, 0, 180),  "grid": (1, 0)},
            "NE":    {"path": "/World/Cameras/Cam_NE", "pos": Gf.Vec3d(dis, dis, h),  "rot": Gf.Vec3d(60, 0, 135),  "grid": (2, 0)},
            "West":  {"path": "/World/Cameras/Cam_W",  "pos": Gf.Vec3d(-dis, 0, h),   "rot": Gf.Vec3d(60, 0, -90),  "grid": (0, 1)},
            "MAIN":  {"path": "/OmniverseKit_Persp",   "pos": None,                  "rot": None,                  "grid": (1, 1)}, # 기본 카메라
            "East":  {"path": "/World/Cameras/Cam_E",  "pos": Gf.Vec3d(dis, 0, h),    "rot": Gf.Vec3d(60, 0, 90),   "grid": (2, 1)},
            "SW":    {"path": "/World/Cameras/Cam_SW", "pos": Gf.Vec3d(-dis, -dis, h), "rot": Gf.Vec3d(60, 0, -45),  "grid": (0, 2)},
            "South": {"path": "/World/Cameras/Cam_S",  "pos": Gf.Vec3d(0, -dis, h),   "rot": Gf.Vec3d(60, 0, 0),    "grid": (1, 2)},
            "SE":    {"path": "/World/Cameras/Cam_SE", "pos": Gf.Vec3d(dis, -dis, h), "rot": Gf.Vec3d(60, 0, 45),   "grid": (2, 2)},
        }

        self._control_window = ui.Window("Viewport Manager", width=250, height=120)
        with self._control_window.frame:
            with ui.VStack(spacing=10, padding=10):
                ui.Button("TOGGLE 3x3 DASHBOARD", clicked_fn=self._toggle_3x3_grid, height=40)
                self._return_btn = ui.Button("RETURN TO DASHBOARD", clicked_fn=self._restore_dashboard, height=30, visible=False)

    def _get_or_create_camera(self, name):
        config = self._camera_configs[name]
        if name == "MAIN":
            return config["path"] # 기본 카메라는 생성 생략
            
        stage = omni.usd.get_context().get_stage()
        cam_path = config["path"]
        
        if not stage.GetPrimAtPath(cam_path):
            cam_prim = UsdGeom.Camera.Define(stage, cam_path)
        else:
            cam_prim = UsdGeom.Camera(stage.GetPrimAtPath(cam_path))

        # Transform 설정 (Double Precision)
        xformable = UsdGeom.Xformable(cam_prim)
        xformable.ClearXformOpOrder()
        xformable.AddTranslateOp(UsdGeom.XformOp.PrecisionDouble).Set(config["pos"])
        xformable.AddRotateXYZOp(UsdGeom.XformOp.PrecisionDouble).Set(config["rot"])
        
        return cam_path

    def _toggle_viewport(self, name):
        if name in self._vp_windows: return
        
        cam_path = self._get_or_create_camera(name)
        new_vp_win = create_viewport_window(name)
        
        if new_vp_win:
            self._vp_windows[name] = new_vp_win
            window = ui.Workspace.get_window(name)
            if window:
                # 대시보드 모드: 타이틀바 및 이동 제한
                window.flags = (ui.WINDOW_FLAGS_NO_TITLE_BAR | ui.WINDOW_FLAGS_NO_MOVE | 
                                ui.WINDOW_FLAGS_NO_RESIZE | ui.WINDOW_FLAGS_NO_SCROLLBAR)
                if hasattr(window.frame, "set_mouse_double_clicked_fn"):
                    window.frame.set_mouse_double_clicked_fn(lambda x, y, b, m, n=name: self._focus_viewport(n))
            
            asyncio.ensure_future(self._apply_camera_settings(name, cam_path))

    async def _apply_camera_settings(self, name, cam_path):
        for _ in range(20): # 3x3은 로딩 부하가 있으므로 시도 횟수 증가
            api = get_viewport_from_window_name(name)
            if api:
                api.camera_path = cam_path
                return
            await asyncio.sleep(0.1)

    def _toggle_3x3_grid(self):
        default_vp = ui.Workspace.get_window("Viewport")
        target_on = len(self._vp_windows) == 0
        
        if default_vp: default_vp.visible = not target_on

        if target_on:
            for name in self._camera_configs.keys():
                self._toggle_viewport(name)
            asyncio.ensure_future(self._setup_3x3_layout())
        else:
            for name in list(self._vp_windows.keys()):
                self._close_viewport(name)
            self._return_btn.visible = False

    async def _setup_3x3_layout(self):
        await asyncio.sleep(0.5)
        main_w = ui.Workspace.get_main_window_width()
        main_h = ui.Workspace.get_main_window_height()
        
        win_w = main_w / 3
        win_h = main_h / 3
        
        for name, config in self._camera_configs.items():
            window = ui.Workspace.get_window(name)
            if window:
                col, row = config["grid"]
                window.position_x = col * win_w
                window.position_y = row * win_h
                window.width = win_w
                window.height = win_h

    def _focus_viewport(self, name):
        """특정 창 더블 클릭 시 메인 뷰포트 확대"""
        for win_name in self._vp_windows:
            win = ui.Workspace.get_window(win_name)
            if win: win.visible = False
        
        default_vp_win = ui.Workspace.get_window("Viewport")
        if default_vp_win:
            default_vp_win.visible = True
            cam_path = self._camera_configs[name]["path"]
            vp_api = get_viewport_from_window_name("Viewport")
            if vp_api: vp_api.camera_path = cam_path
        
        self._return_btn.visible = True

    def _restore_dashboard(self):
        default_vp_win = ui.Workspace.get_window("Viewport")
        if default_vp_win: default_vp_win.visible = False
        for win_name in self._vp_windows:
            win = ui.Workspace.get_window(win_name)
            if win: win.visible = True
        self._return_btn.visible = False

    def _close_viewport(self, name):
        if name in self._vp_windows:
            self._vp_windows[name].destroy()
            del self._vp_windows[name]

    def on_shutdown(self):
        for name in list(self._vp_windows.keys()): self._close_viewport(name)
        default_vp = ui.Workspace.get_window("Viewport")
        if default_vp: default_vp.visible = True