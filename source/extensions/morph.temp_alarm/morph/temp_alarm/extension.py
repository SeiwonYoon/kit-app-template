# morph/temp_alarm/extension.py
# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

import math
import time
from typing import Dict, Optional, Set, Tuple, List

import carb
import omni.ext
import omni.kit.app
import omni.ui as ui
import omni.usd
import omni.kit.viewport.utility as vpu
from pxr import Usd, UsdGeom, Gf

from morph.pick_filter.core import register_temperature_listener, unregister_temperature_listener

TEMP_ATTR = "hynix:temperature"

# Selection Group preset (outline/shade: RGBA 0~1)
PRESETS = {
    "none":        dict(outline=(0.0, 0.0, 0.0, 0.0), shade=(0.0, 0.0, 0.0, 0.0), desc="표시 없음"),
    "warning":     dict(outline=(0.95, 0.65, 0.10, 1.0), shade=(1.00, 0.55, 0.00, 0.22), desc="경고"),
    "error":       dict(outline=(0.93, 0.11, 0.14, 1.0), shade=(0.50, 0.00, 0.00, 0.35), desc="오류"),
    "alarm_pulse": dict(outline=(1.00, 0.20, 0.20, 1.0), shade=(1.00, 0.10, 0.10, 0.18), anim="pulse", period=1.0, desc="알람(펄스)"),
}

DEFAULT_THRESHOLDS = dict(warn=70.0, error=90.0, alarm=110.0)


class MyExtension(omni.ext.IExt):
    def on_startup(self, ext_id):
        self._ext_id = ext_id

        # group_id per preset
        self._groups: Dict[str, int] = {}
        self._anim_presets: Set[str] = set()

        # applied state: path -> preset (for Gprim targets)
        self._applied: Dict[str, str] = {}

        # persistent tint state (session layer displayColor)
        self._tint_paths: Set[str] = set()
        self._use_persistent_tint: bool = True

        # controls
        self._enabled: bool = True
        self._root: str = "/World"
        self._interval_sec: float = 0.35
        self._thresholds = dict(DEFAULT_THRESHOLDS)

        # visibility boost (debug): selection-group가 약하게 보이는 환경 대비
        self._force_visible: bool = False

        # --- Viewport selection disable (NEW) ---
        # disable_selection은 scope 객체를 반환하며, 그 객체를 잡고 있는 동안 비활성 유지됨
        self._disable_viewport_selection: bool = False
        self._disable_click: bool = True
        self._disable_sel_scope = None
        self._pending_apply_viewport_disable: bool = False

        # ui
        self._window = ui.Window("Temp Alarm (hynix:temperature)", width=760, height=360)
        self._window.visible = True
        with self._window.frame:
            with ui.VStack(padding=10, spacing=8):
                with ui.HStack(height=24, spacing=10):
                    ui.Label("Enabled", width=90)
                    self._chk_enabled = ui.CheckBox()
                    self._chk_enabled.model.set_value(True)
                    self._chk_enabled.model.add_value_changed_fn(self._on_enabled_changed)

                    ui.Label("Persistent Tint", width=120)
                    self._chk_tint = ui.CheckBox()
                    self._chk_tint.model.set_value(True)
                    self._chk_tint.model.add_value_changed_fn(self._on_persistent_tint_changed)

                    ui.Label("Force Visible", width=110)
                    self._chk_force = ui.CheckBox()
                    self._chk_force.model.set_value(False)
                    self._chk_force.model.add_value_changed_fn(self._on_force_visible_changed)

                    ui.Spacer()
                    ui.Button("Rescan", width=120, clicked_fn=self._force_rescan_once)

                # --- Viewport selection disable UI (NEW) ---
                with ui.HStack(height=24, spacing=10):
                    ui.Label("Disable Viewport Selection", width=210)
                    self._chk_disable_sel = ui.CheckBox()
                    self._chk_disable_sel.model.set_value(False)
                    self._chk_disable_sel.model.add_value_changed_fn(self._on_disable_sel_changed)

                    ui.Label("Disable Click", width=110)
                    self._chk_disable_click = ui.CheckBox()
                    self._chk_disable_click.model.set_value(True)
                    self._chk_disable_click.model.add_value_changed_fn(self._on_disable_click_changed)

                    ui.Label("(drag box + optional click)", width=0)

                with ui.HStack(height=24, spacing=10):
                    ui.Label("Root", width=90)
                    self._root_field = ui.StringField()
                    self._root_field.model.set_value(self._root)
                    self._root_field.model.add_value_changed_fn(self._on_root_changed)

                with ui.HStack(height=24, spacing=10):
                    ui.Label("Warn≥", width=90)
                    self._warn = ui.FloatField()
                    self._warn.model.set_value(float(self._thresholds["warn"]))
                    self._warn.model.add_value_changed_fn(lambda m: self._on_thr_changed("warn", m))

                    ui.Label("Error≥", width=70)
                    self._error = ui.FloatField()
                    self._error.model.set_value(float(self._thresholds["error"]))
                    self._error.model.add_value_changed_fn(lambda m: self._on_thr_changed("error", m))

                    ui.Label("Alarm≥", width=70)
                    self._alarm = ui.FloatField()
                    self._alarm.model.set_value(float(self._thresholds["alarm"]))
                    self._alarm.model.add_value_changed_fn(lambda m: self._on_thr_changed("alarm", m))

                self._status = ui.Label("status: idle", height=18)
                self._status2 = ui.Label("", height=18)
                self._status3 = ui.Label("", height=18)

        # update loop (anim + periodic scan + viewport disable apply)
        self._t0 = time.time()
        self._last_scan_t = 0.0
        self._update_sub = omni.kit.app.get_app().get_update_event_stream().create_subscription_to_pop(
            self._on_update, name="temp_alarm_update"
        )

        # ✅ 즉시 신호 구독 (pick_filter.core에서 온도 변경 시 즉시 호출됨)
        self._temp_listener = self._on_temp_changed_signal
        register_temperature_listener(self._temp_listener)

        carb.log_info("[morph.temp_alarm] startup")

    def on_shutdown(self):
        carb.log_info("[morph.temp_alarm] shutdown")

        try:
            unregister_temperature_listener(self._temp_listener)
        except Exception:
            pass

        # viewport selection disable scope release
        self._disable_sel_scope = None

        # persistent tint 원복(세션 레이어에서만 Clear)
        try:
            self._clear_all_persistent_tints()
        except Exception:
            pass

        try:
            self._update_sub = None
        except Exception:
            pass

        self._window = None
        self._groups.clear()
        self._anim_presets.clear()
        self._applied.clear()

    # ---------------- UI callbacks ----------------
    def _on_enabled_changed(self, model):
        self._enabled = bool(model.get_value_as_bool())
        if not self._enabled:
            self._apply_none_to_all_known()
        self._status.text = f"status: enabled={self._enabled}"

    def _on_persistent_tint_changed(self, model):
        self._use_persistent_tint = bool(model.get_value_as_bool())
        if not self._use_persistent_tint:
            self._clear_all_persistent_tints()
        self._status.text = f"status: persistent_tint={self._use_persistent_tint}"

    def _on_force_visible_changed(self, model):
        self._force_visible = bool(model.get_value_as_bool())
        self._reapply_group_colors()
        self._status.text = f"status: force_visible={self._force_visible} (colors reapplied)"

    def _on_disable_sel_changed(self, model):
        self._disable_viewport_selection = bool(model.get_value_as_bool())
        self._request_apply_viewport_disable()
        self._status.text = f"status: disable_viewport_selection={self._disable_viewport_selection}"

    def _on_disable_click_changed(self, model):
        self._disable_click = bool(model.get_value_as_bool())
        # selection disable이 이미 켜져있다면 scope를 갱신해야 함
        self._request_apply_viewport_disable(force_recreate=True)
        self._status.text = f"status: disable_click={self._disable_click}"

    def _on_root_changed(self, model):
        self._root = (model.get_value_as_string() or "/World").strip() or "/World"
        self._status.text = f"status: root={self._root}"
        self._force_rescan_once()

    def _on_thr_changed(self, key: str, model):
        try:
            self._thresholds[key] = float(model.get_value_as_float())
        except Exception:
            pass
        self._force_rescan_once()

    def _force_rescan_once(self):
        self._last_scan_t = 0.0

    # ---------------- viewport selection disable (NEW) ----------------
    def _request_apply_viewport_disable(self, force_recreate: bool = False):
        # 다음 update에서 viewport가 준비되면 적용되도록
        self._pending_apply_viewport_disable = True
        # click 옵션 바뀐 경우 scope 재생성 필요
        if force_recreate:
            self._disable_sel_scope = None

    def _apply_viewport_disable_if_ready(self):
        """
        active viewport를 기준으로 선택 비활성화 적용.
        viewport가 아직 없으면 다음 프레임에 재시도.
        """
        if not self._pending_apply_viewport_disable:
            return

        vp = None
        try:
            vp = vpu.get_active_viewport()
        except Exception:
            vp = None

        if not vp:
            # viewport가 아직 준비 안됨 → 다음 프레임 재시도
            return

        # ✅ 적용/해제
        if self._disable_viewport_selection:
            if self._disable_sel_scope is None:
                try:
                    self._disable_sel_scope = vpu.disable_selection(vp, disable_click=self._disable_click)
                except Exception:
                    self._disable_sel_scope = None
        else:
            # scope 해제하면 selection 복구
            self._disable_sel_scope = None

        self._pending_apply_viewport_disable = False
        if getattr(self, "_status2", None):
            self._status2.text = f"viewport selection disabled={self._disable_viewport_selection}, disable_click={self._disable_click}"

    # ---------------- signal: immediate apply (subtree) ----------------
    def _on_temp_changed_signal(self, path: str, value: float):
        """
        ✅ 즉시 신호:
        - 온도 변경 prim이 Xform/Scope여도 OK
        - 해당 prim의 하위 Gprim(Mesh 포함) 전체에 즉시 preset 적용
        - 이후 주기 스캔에서 "own_temp 우선 / 상속" 규칙으로 정렬 유지
        """
        if not self._enabled:
            return

        ctx = omni.usd.get_context()
        stage = ctx.get_stage() if ctx else None
        if not stage:
            return

        root = stage.GetPrimAtPath(path)
        if not root or not root.IsValid():
            return

        preset = self._resolve_preset_for_temp(float(value))
        gid = self._ensure_group(preset)

        applied = 0
        tinted = 0

        for prim in Usd.PrimRange(root):
            if not prim or not prim.IsValid():
                continue
            if not prim.IsA(UsdGeom.Gprim):
                continue

            p = prim.GetPath().pathString

            # selection group (outline/shade)
            try:
                ctx.set_selection_group(gid, p)
            except Exception:
                pass

            # persistent tint (session layer displayColor)
            if self._use_persistent_tint:
                if self._apply_persistent_tint_gprim(prim, preset):
                    tinted += 1

            self._applied[p] = preset
            applied += 1

        if getattr(self, "_status", None):
            self._status.text = f"status: SIGNAL subtree applied={applied}"
        if getattr(self, "_status2", None):
            self._status2.text = f"{path} T={float(value):.1f} -> preset={preset} (gid={gid})"
        if getattr(self, "_status3", None):
            self._status3.text = f"persistent_tint={self._use_persistent_tint}, tinted={tinted}"

    # ---------------- update loop ----------------
    def _on_update(self, _e):
        # 0) viewport selection disable apply (NEW)
        self._apply_viewport_disable_if_ready()

        # 1) animated presets alpha update
        self._update_anim_groups()

        # 2) periodic scan/apply (effective temperature inheritance)
        if not self._enabled:
            return

        now = time.time()
        if (now - self._last_scan_t) < self._interval_sec:
            return
        self._last_scan_t = now

        applied_n, tinted_n = self._scan_and_apply()
        self._status.text = f"status: scan applied={applied_n}, root={self._root}"
        self._status3.text = f"persistent_tint={self._use_persistent_tint}, tinted={tinted_n}, tracked={len(self._applied)}"

    def _update_anim_groups(self):
        if not self._anim_presets:
            return

        ctx = omni.usd.get_context()
        t = time.time() - self._t0

        for preset_name in list(self._anim_presets):
            gid = self._groups.get(preset_name)
            p = PRESETS.get(preset_name)
            if gid is None or not p:
                continue

            anim = p.get("anim")
            period = float(p.get("period", 1.0))
            period = max(period, 1e-3)

            outline = list(self._preset_outline(preset_name))
            shade = list(self._preset_shade(preset_name))

            if anim == "pulse":
                k = 0.5 + 0.5 * math.sin((t / period) * 2.0 * math.pi)
                outline[3] = max(0.05, min(1.0, outline[3] * (0.35 + 0.65 * k)))
                shade[3] = max(0.0, min(0.95, shade[3] * (0.25 + 0.75 * k)))

            try:
                ctx.set_selection_group_outline_color(gid, carb.Float4(outline))
                ctx.set_selection_group_shade_color(gid, carb.Float4(shade))
            except Exception:
                pass

    # ---------------- core: presets/groups ----------------
    def _preset_outline(self, preset: str):
        p = PRESETS.get(preset, PRESETS["none"])
        outline = list(p["outline"])
        if self._force_visible and preset != "none":
            outline[3] = max(outline[3], 1.0)
        return outline

    def _preset_shade(self, preset: str):
        p = PRESETS.get(preset, PRESETS["none"])
        shade = list(p["shade"])
        if self._force_visible and preset != "none":
            shade[3] = max(shade[3], 0.45)
        return shade

    def _reapply_group_colors(self):
        ctx = omni.usd.get_context()
        for preset, gid in list(self._groups.items()):
            try:
                ctx.set_selection_group_outline_color(gid, carb.Float4(self._preset_outline(preset)))
                ctx.set_selection_group_shade_color(gid, carb.Float4(self._preset_shade(preset)))
            except Exception:
                pass

    def _ensure_group(self, preset: str) -> int:
        preset = preset if preset in PRESETS else "none"
        if preset in self._groups:
            return self._groups[preset]

        ctx = omni.usd.get_context()
        gid = ctx.register_selection_group()

        try:
            ctx.set_selection_group_outline_color(gid, carb.Float4(self._preset_outline(preset)))
            ctx.set_selection_group_shade_color(gid, carb.Float4(self._preset_shade(preset)))
        except Exception:
            pass

        self._groups[preset] = gid
        if PRESETS[preset].get("anim"):
            self._anim_presets.add(preset)
        return gid

    def _resolve_preset_for_temp(self, temp: float) -> str:
        warn = float(self._thresholds.get("warn", 70.0))
        err = float(self._thresholds.get("error", 90.0))
        alm = float(self._thresholds.get("alarm", 110.0))

        if temp >= alm:
            return "alarm_pulse"
        if temp >= err:
            return "error"
        if temp >= warn:
            return "warning"
        return "none"

    # ---------------- temp read ----------------
    def _read_temp(self, prim) -> Optional[float]:
        try:
            a = prim.GetAttribute(TEMP_ATTR)
            if not a or not a.IsValid():
                return None
            v = a.Get()
            if v is None:
                return None
            return float(v)
        except Exception:
            return None

    # ---------------- persistent tint (session layer) ----------------
    def _apply_persistent_tint_gprim(self, prim, preset: str) -> bool:
        """
        displayColor/displayOpacity를 Session Layer에 기록해서
        선택/드래그/하이라이트 리셋과 무관하게 항상 표시되게 한다.

        반환: 실제로 Set/Clear를 시도한 경우 True
        """
        if not prim or not prim.IsValid():
            return False
        if not prim.IsA(UsdGeom.Gprim):
            return False

        stage = prim.GetStage()
        if not stage:
            return False

        gprim = UsdGeom.Gprim(prim)

        # session layer edit target
        try:
            with Usd.EditContext(stage, stage.GetSessionLayer()):
                return self._apply_persistent_tint_on_edit_target(gprim, prim.GetPath().pathString, preset)
        except Exception:
            # session layer가 막혔거나 예외면, 현 edit target에라도 기록(환경별 예외 대비)
            try:
                with Usd.EditContext(stage, stage.GetEditTarget().GetLayer()):
                    return self._apply_persistent_tint_on_edit_target(gprim, prim.GetPath().pathString, preset)
            except Exception:
                return False

    def _apply_persistent_tint_on_edit_target(self, gprim: UsdGeom.Gprim, path: str, preset: str) -> bool:
        if preset == "none":
            try:
                gprim.GetDisplayColorAttr().Clear()
            except Exception:
                pass
            try:
                gprim.GetDisplayOpacityAttr().Clear()
            except Exception:
                pass
            if path in self._tint_paths:
                self._tint_paths.remove(path)
            return True

        p = PRESETS.get(preset, PRESETS["warning"])
        r, g, b, a = p["shade"]

        oa = float(max(0.25, min(1.0, float(a))))

        try:
            gprim.GetDisplayColorAttr().Set([Gf.Vec3f(float(r), float(g), float(b))])
        except Exception:
            pass
        try:
            gprim.GetDisplayOpacityAttr().Set([oa])
        except Exception:
            pass

        self._tint_paths.add(path)
        return True

    def _clear_all_persistent_tints(self):
        ctx = omni.usd.get_context()
        stage = ctx.get_stage() if ctx else None
        if not stage:
            self._tint_paths.clear()
            return

        try:
            with Usd.EditContext(stage, stage.GetSessionLayer()):
                self._clear_tint_paths_in_stage(stage)
        except Exception:
            try:
                with Usd.EditContext(stage, stage.GetEditTarget().GetLayer()):
                    self._clear_tint_paths_in_stage(stage)
            except Exception:
                pass

        self._tint_paths.clear()

    def _clear_tint_paths_in_stage(self, stage):
        for path in list(self._tint_paths):
            prim = stage.GetPrimAtPath(path)
            if not prim or not prim.IsValid() or not prim.IsA(UsdGeom.Gprim):
                continue
            gprim = UsdGeom.Gprim(prim)
            try:
                gprim.GetDisplayColorAttr().Clear()
            except Exception:
                pass
            try:
                gprim.GetDisplayOpacityAttr().Clear()
            except Exception:
                pass

    # ---------------- scan/apply with inheritance ----------------
    def _scan_and_apply(self) -> Tuple[int, int]:
        """
        ✅ 상속(effective temperature):
        - prim 자체에 온도(attr)가 있으면 그것이 우선
        - 없으면 상위에서 내려온 inherited_temp 사용

        ✅ 적용 대상:
        - UsdGeom.Gprim (Mesh 포함)만 적용

        ✅ 표시:
        - selection group (outline/shade): 환경에 따라 리셋될 수 있음
        - persistent tint (session layer displayColor): 선택/드래그와 무관하게 항상 유지
        """
        ctx = omni.usd.get_context()
        stage = ctx.get_stage() if ctx else None
        if not stage:
            if getattr(self, "_status2", None):
                self._status2.text = "stage: None"
            return 0, 0

        root_path = (self._root or "/World").strip() or "/World"
        root_prim = stage.GetPrimAtPath(root_path) if root_path != "/" else stage.GetPseudoRoot()
        if not root_prim or not root_prim.IsValid():
            if getattr(self, "_status2", None):
                self._status2.text = f"root invalid: {root_path}"
            return 0, 0

        applied_n = 0
        tinted_n = 0
        seen_paths: Set[str] = set()

        # DFS stack: (prim, inherited_temp)
        stack: List[Tuple[Usd.Prim, Optional[float]]] = [(root_prim, None)]

        while stack:
            prim, inherited_temp = stack.pop()
            if not prim or not prim.IsValid():
                continue

            own_temp = self._read_temp(prim)
            eff_temp = own_temp if own_temp is not None else inherited_temp

            if prim.IsA(UsdGeom.Gprim) and eff_temp is not None:
                path = prim.GetPath().pathString
                preset = self._resolve_preset_for_temp(float(eff_temp))
                seen_paths.add(path)

                prev = self._applied.get(path)
                if prev != preset:
                    gid = self._ensure_group(preset)

                    try:
                        ctx.set_selection_group(gid, path)
                    except Exception:
                        pass

                    if self._use_persistent_tint:
                        if self._apply_persistent_tint_gprim(prim, preset):
                            tinted_n += 1

                    self._applied[path] = preset

                applied_n += 1

            try:
                children = prim.GetChildren()
            except Exception:
                children = []

            for c in reversed(children):
                stack.append((c, eff_temp))

        # stale cleanup
        stale = [p for p in list(self._applied.keys()) if p not in seen_paths]
        if stale:
            none_gid = self._ensure_group("none")
            for p in stale:
                try:
                    ctx.set_selection_group(none_gid, p)
                except Exception:
                    pass

                if self._use_persistent_tint:
                    prim = stage.GetPrimAtPath(p)
                    if prim and prim.IsValid() and prim.IsA(UsdGeom.Gprim):
                        if self._apply_persistent_tint_gprim(prim, "none"):
                            tinted_n += 1

                self._applied.pop(p, None)

        if getattr(self, "_status2", None):
            self._status2.text = f"tracked(gprim)={len(self._applied)} (force_visible={self._force_visible})"

        return applied_n, tinted_n

    def _apply_none_to_all_known(self):
        ctx = omni.usd.get_context()
        stage = ctx.get_stage() if ctx else None

        none_gid = self._ensure_group("none")
        for p in list(self._applied.keys()):
            try:
                ctx.set_selection_group(none_gid, p)
            except Exception:
                pass

            if self._use_persistent_tint and stage:
                prim = stage.GetPrimAtPath(p)
                if prim and prim.IsValid() and prim.IsA(UsdGeom.Gprim):
                    self._apply_persistent_tint_gprim(prim, "none")

        self._applied.clear()
