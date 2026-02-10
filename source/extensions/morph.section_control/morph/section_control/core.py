# morph/section_control/core.py
# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

import time
import traceback
from typing import Optional, Dict, Any

import carb
import carb.events
import carb.settings
import omni.usd
import omni.kit.app
import omni.kit.livestream.messaging as messaging

from pxr import Gf, Usd, UsdGeom, Sdf
from omni.kit.window.section.common import SectionManager
from omni.kit.window.section.common import (
    SETTING_SECTION_ENABLED,
    SETTING_SECTION_DIRECTION,
    SETTING_SECTION_ALWAYS_DISPLAY,
    SETTING_SECTION_LIGHT,
)

try:
    from omni.usd import StageEventType
except Exception:
    StageEventType = None


def _ts():
    return time.strftime("%H:%M:%S", time.localtime())


def _log(msg: str):
    carb.log_warn(f"[section_control] {_ts()} {msg}")


def _log_exc(prefix: str, ex: Exception):
    tb = traceback.format_exc()
    carb.log_warn(f"[section_control] {_ts()} {prefix}: {ex}\n{tb}")


class MessageBusAPI:
    """
    Web client sends event: <name>_request payload { id, ...args }
    Kit responds: <name>_response payload { id, response } or { id, error }
    """

    def __init__(self):
        self._subs = {}
        self._bus = omni.kit.app.get_app().get_message_bus_event_stream()

    def destroy(self):
        for sub in self._subs.values():
            try:
                sub.unsubscribe()
            except Exception:
                pass
        self._subs.clear()

    def request(self, func=None, *, name=None):
        def _decorator(f):
            api_name = name or f.__name__
            req_name = f"{api_name}_request"
            resp_name = f"{api_name}_response"

            req_type = carb.events.type_from_string(req_name)
            resp_type = carb.events.type_from_string(resp_name)

            messaging.register_event_type_to_send(resp_name)

            def _on_event(e: carb.events.IEvent):
                payload = dict(e.payload.get_dict())
                call_id = payload.pop("id", -1)

                try:
                    _log(f"bus recv {req_name} id={call_id} payload={payload}")
                    result = f(**payload)
                    out = {"id": call_id, "response": result}
                except Exception as ex:
                    _log_exc(f"bus handler error ({api_name})", ex)
                    out = {"id": call_id, "error": str(ex)}

                if call_id != -1:
                    self._bus.dispatch(resp_type, payload=out)
                    self._bus.pump()

            sub = self._bus.create_subscription_to_pop_by_type(req_type, _on_event, name=req_name)
            self._subs[req_name] = sub
            return f

        if func is None:
            return _decorator
        return _decorator(func)


class SectionController:
    AXES = ("X", "Y", "Z")

    def __init__(self):
        self._settings = carb.settings.get_settings()

        self._sec_mgr: Optional[SectionManager] = None
        self._widget_path: Optional[Sdf.Path] = None
        self._last_stage_id: Optional[int] = None

        self._enabled = bool(self._settings.get(SETTING_SECTION_ENABLED) or False)
        self._axis = "X"
        self._flip = False
        self._offset = 0.0

        self._always_display = bool(self._settings.get(SETTING_SECTION_ALWAYS_DISPLAY) or False)
        self._light = bool(self._settings.get(SETTING_SECTION_LIGHT) or False)

        _log(
            "controller init "
            f"enabled={self._enabled} axis={self._axis} flip={self._flip} offset={self._offset} "
            f"always_display={self._always_display} light={self._light}"
        )

    # ---------- stage helpers ----------
    def _get_stage(self) -> Optional[Usd.Stage]:
        return omni.usd.get_context().get_stage()

    def _stage_identity(self, stage: Optional[Usd.Stage]) -> Optional[int]:
        return None if stage is None else id(stage)

    def is_stage_ready(self) -> bool:
        return self._get_stage() is not None

    def invalidate(self, reason: str):
        _log(
            f"invalidate(reason='{reason}') "
            f"sec_mgr={'Y' if self._sec_mgr else 'N'} widget_path={self._widget_path}"
        )
        self._sec_mgr = None
        self._widget_path = None
        self._last_stage_id = None

    def _ensure_ready(self) -> bool:
        stage = self._get_stage()
        if stage is None:
            _log("_ensure_ready: stage is None")
            return False

        stage_id = self._stage_identity(stage)
        if self._last_stage_id is None:
            self._last_stage_id = stage_id
        elif stage_id != self._last_stage_id:
            _log(f"_ensure_ready: stage swapped old={self._last_stage_id} new={stage_id} -> invalidate")
            self.invalidate("stage_swapped")
            self._last_stage_id = stage_id

        if self._sec_mgr is None:
            try:
                sec_ext = omni.kit.app.get_app().get_extension_manager().get_extension("omni.kit.window.section")
                _log(f"_ensure_ready: omni.kit.window.section ext present? {'Y' if sec_ext else 'N'}")
            except Exception as ex:
                _log(f"_ensure_ready: extension manager check failed: {ex}")

            self._sec_mgr = SectionManager()
            _log("_ensure_ready: SectionManager created")

        if self._widget_path is None:
            try:
                w = self._sec_mgr.get_section_widget_prim(True)
                _log(f"_ensure_ready: get_section_widget_prim(True) => {type(w)} {w}")
            except Exception as ex:
                _log_exc("_ensure_ready: get_section_widget_prim failed", ex)
                return False

            try:
                if isinstance(w, Usd.Prim):
                    path = w.GetPath()
                elif isinstance(w, Sdf.Path):
                    path = w
                else:
                    path = Sdf.Path(str(w))
            except Exception as ex:
                _log_exc(f"_ensure_ready: normalize widget path failed (w={w})", ex)
                return False

            self._widget_path = path
            _log(f"_ensure_ready: widget_path={self._widget_path}")

        try:
            prim = stage.GetPrimAtPath(self._widget_path)
            _log(f"_ensure_ready: prim valid={prim.IsValid() if prim else False} prim={prim}")
        except Exception as ex:
            _log_exc(f"_ensure_ready: stage.GetPrimAtPath failed (path={self._widget_path})", ex)
            return False

        return True

    def _get_widget_prim(self) -> Optional[Usd.Prim]:
        if not self._ensure_ready():
            return None
        stage = self._get_stage()
        if stage is None or self._widget_path is None:
            return None
        try:
            prim = stage.GetPrimAtPath(self._widget_path)
            if not prim or not prim.IsValid():
                _log(f"_get_widget_prim: invalid prim at {self._widget_path}")
                return None
            return prim
        except Exception as ex:
            _log_exc(f"_get_widget_prim: GetPrimAtPath failed path={self._widget_path}", ex)
            return None

    # ---------- apply ----------
    def _apply_widget_translation(self, axis: str, offset: float) -> bool:
        prim = self._get_widget_prim()
        if prim is None:
            _log("_apply_widget_translation: widget prim is None")
            return False

        try:
            xform = UsdGeom.Xformable(prim)
            ops = xform.GetOrderedXformOps()
            t_op = None
            for op in ops:
                if op.GetOpType() == UsdGeom.XformOp.TypeTranslate:
                    t_op = op
                    break
            if t_op is None:
                t_op = xform.AddTranslateOp()
                _log("_apply_widget_translation: created TranslateOp")

            v = Gf.Vec3d(0.0, 0.0, 0.0)
            if axis == "X":
                v[0] = offset
            elif axis == "Y":
                v[1] = offset
            elif axis == "Z":
                v[2] = offset

            t_op.Set(v)
            _log(f"_apply_widget_translation: set translate {v} on {prim.GetPath()}")
            return True
        except Exception as ex:
            _log_exc("_apply_widget_translation failed", ex)
            return False

    def _apply_offset_to_stage(self) -> bool:
        if not self._ensure_ready():
            _log("_apply_offset_to_stage: ensure_ready failed")
            return False
        signed_offset = -self._offset if self._flip else self._offset
        return self._apply_widget_translation(self._axis, signed_offset)

    def _apply_axis_to_stage(self) -> bool:
        if not self._ensure_ready():
            _log("_apply_axis_to_stage: ensure_ready failed")
            return False
        try:
            _log(f"_apply_axis_to_stage: calling align_widget({self._axis.lower()})")
            self._sec_mgr.align_widget(self._axis.lower())
            _log("_apply_axis_to_stage: align_widget OK")
            return True
        except Exception as ex:
            _log_exc("_apply_axis_to_stage: align_widget failed", ex)
            return False

    def _apply_all_to_stage(self) -> bool:
        if not self._ensure_ready():
            _log("_apply_all_to_stage: ensure_ready failed")
            return False
        ok_axis = self._apply_axis_to_stage()
        ok_off = self._apply_offset_to_stage()
        _log(f"_apply_all_to_stage: ok_axis={ok_axis} ok_offset={ok_off}")
        return ok_axis and ok_off

    # ---------- external state API ----------
    def set_enabled(self, enabled: bool) -> Dict[str, Any]:
        self._enabled = bool(enabled)
        _log(f"set_enabled({self._enabled})")
        self._settings.set(SETTING_SECTION_ENABLED, self._enabled)
        if self._enabled:
            self._settings.set(SETTING_SECTION_ALWAYS_DISPLAY, True)
        return self.get_state()

    def set_axis(self, axis: str) -> Dict[str, Any]:
        axis = (axis or "").upper()
        if axis not in self.AXES:
            raise ValueError(f"axis must be one of {self.AXES}")
        self._axis = axis
        _log(f"set_axis({self._axis})")
        return self.get_state()

    def set_flip(self, flip: bool) -> Dict[str, Any]:
        self._flip = bool(flip)
        direction = 0 if self._flip else 1
        _log(f"set_flip({self._flip}) direction={direction}")
        self._settings.set(SETTING_SECTION_DIRECTION, direction)
        return self.get_state()

    def set_offset(self, offset: float) -> Dict[str, Any]:
        self._offset = float(offset)
        _log(f"set_offset({self._offset})")
        return self.get_state()

    def get_state(self) -> Dict[str, Any]:
        return {
            "enabled": self._enabled,
            "axis": self._axis,
            "flip": self._flip,
            "offset": self._offset,
            "widget_path": str(self._widget_path) if self._widget_path else "",
            "stage_ready": self.is_stage_ready(),
            "sec_mgr_ready": bool(self._sec_mgr is not None),
        }

    def apply_once_if_possible(self, attempt: int) -> bool:
        _log(f"apply_once attempt={attempt} enabled={self._enabled} stage_ready={self.is_stage_ready()}")
        if not self._enabled:
            _log("apply_once: disabled -> skip apply (DONE)")
            return True
        return self._apply_all_to_stage()
