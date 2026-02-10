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

    # 필요 시 샘플과 동일한 축 매핑을 쓰고 싶으면 True로 전환
    # (X->x, Y->z, Z->y). 기본은 직매핑(X->x, Y->y, Z->z)
    USE_SAMPLE_AXIS_MAPPING = False

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

        # -----------------------------
        # ✅ 누적(드리프트) 방지용 상태
        # base_world_pos: offset=0 기준 월드 위치(align 직후 캡처)
        # applied_axis: 마지막으로 실제 align 적용한 axis
        # applied_signed_offset: 마지막으로 stage에 적용한 signed_offset
        # dirty flags: 값이 바뀔 때만 stage에 반영 (매 tick 재적용 금지)
        # -----------------------------
        self._base_world_pos: Optional[Gf.Vec3d] = None
        self._applied_axis: Optional[str] = None
        self._applied_signed_offset: float = 0.0

        self._dirty_axis: bool = True
        self._dirty_offset: bool = True

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

        # ✅ stage/widget이 바뀌면 기준점/적용상태/dirty 리셋
        self._base_world_pos = None
        self._applied_axis = None
        self._applied_signed_offset = 0.0
        self._dirty_axis = True
        self._dirty_offset = True

    def _axis_to_align_arg(self, axis: str) -> str:
        axis = (axis or "X").upper()
        if not self.USE_SAMPLE_AXIS_MAPPING:
            return axis.lower()
        if axis == "X":
            return "x"
        if axis == "Y":
            return "z"
        return "y"

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
            self._sec_mgr = SectionManager()
            _log("_ensure_ready: SectionManager created")

        if self._widget_path is None:
            try:
                w = self._sec_mgr.get_section_widget_prim(True)
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

    # ---------- section edit context ----------
    def _with_section_edit_context(self):
        class _NoOpCtx:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        if self._sec_mgr is None:
            return _NoOpCtx()

        if hasattr(self._sec_mgr, "_get_section_edit_context"):
            try:
                return self._sec_mgr._get_section_edit_context()
            except Exception as ex:
                _log(f"_with_section_edit_context: failed, fallback no-op: {ex}")
                return _NoOpCtx()

        return _NoOpCtx()

    # ---------- widget world pos ----------
    def _get_widget_world_translation(self, prim: Usd.Prim) -> Gf.Vec3d:
        try:
            xform = UsdGeom.Xformable(prim)
            m = xform.ComputeLocalToWorldTransform(Usd.TimeCode.Default())
            t = m.ExtractTranslation()
            return Gf.Vec3d(t[0], t[1], t[2])
        except Exception as ex:
            _log_exc("_get_widget_world_translation failed", ex)
            return Gf.Vec3d(0.0, 0.0, 0.0)

    # ---------- apply ----------
    def _apply_axis_to_stage_if_needed(self) -> bool:
        """
        ✅ 축 align은 axis 값이 바뀔 때만 수행 (매 tick 수행 금지)
        align 직후의 위젯 위치를 base(offset=0 기준)로 캡처합니다.
        """
        if not self._ensure_ready():
            _log("_apply_axis_to_stage_if_needed: ensure_ready failed")
            return False

        # dirty가 아니면 스킵
        if not self._dirty_axis and self._applied_axis == self._axis:
            return True

        try:
            arg = self._axis_to_align_arg(self._axis)
            _log(f"_apply_axis_to_stage_if_needed: align_widget({arg})")
            with self._with_section_edit_context():
                self._sec_mgr.align_widget(arg)

            # align 직후 base 캡처 (offset=0 기준)
            prim = self._get_widget_prim()
            self._base_world_pos = self._get_widget_world_translation(prim) if prim is not None else None

            self._applied_axis = self._axis
            self._applied_signed_offset = 0.0  # align 직후는 offset=0 상태로 간주
            self._dirty_axis = False

            # align 했으면 offset 재적용 필요
            self._dirty_offset = True

            _log(f"_apply_axis_to_stage_if_needed: DONE axis={self._axis} base={self._base_world_pos}")
            return True
        except Exception as ex:
            _log_exc("_apply_axis_to_stage_if_needed failed", ex)
            return False

    def _apply_offset_to_stage_absolute_if_needed(self) -> bool:
        """
        ✅ 누적(드리프트) 방지 핵심:
        - offset은 항상 base_world_pos(=offset 0 기준) + offset(절대) 으로 계산
        - flip은 '방향'만 변경하고 위치(offset)는 변경하지 않는다
        - 값이 바뀔 때만 set_widget_position 호출 (매 tick 재적용 금지)
        """
        if not self._ensure_ready():
            _log("_apply_offset_to_stage_absolute_if_needed: ensure_ready failed")
            return False

        if not self._dirty_offset:
            return True

        prim = self._get_widget_prim()
        if prim is None:
            _log("_apply_offset_to_stage_absolute_if_needed: widget prim is None")
            return False

        try:
            # ✅ 핵심 변경: flip에 따른 부호 반전 제거 (위치 고정)
            signed_offset = self._offset

            # base가 없으면 "현재 위치 - (이전에 적용된 offset)"으로 base 복원
            if self._base_world_pos is None:
                cur = self._get_widget_world_translation(prim)
                base = Gf.Vec3d(cur[0], cur[1], cur[2])

                # 현재 stage가 applied_signed_offset 상태라고 보고 0점 복원
                if self._applied_signed_offset != 0.0:
                    if self._axis == "X":
                        base[0] -= self._applied_signed_offset
                    elif self._axis == "Y":
                        base[1] -= self._applied_signed_offset
                    else:
                        base[2] -= self._applied_signed_offset

                self._base_world_pos = base
                _log(f"_apply_offset_to_stage_absolute_if_needed: base restored={self._base_world_pos} (cur={cur})")

            # tgt = base + offset(절대)
            tgt = Gf.Vec3d(self._base_world_pos[0], self._base_world_pos[1], self._base_world_pos[2])
            if self._axis == "X":
                tgt[0] += signed_offset
            elif self._axis == "Y":
                tgt[1] += signed_offset
            else:
                tgt[2] += signed_offset

            with self._with_section_edit_context():
                self._sec_mgr.set_widget_position(tgt)

            self._applied_signed_offset = signed_offset
            self._dirty_offset = False

            _log(f"_apply_offset_to_stage_absolute_if_needed: tgt={tgt} offset={signed_offset} base={self._base_world_pos}")
            return True
        except Exception as ex:
            _log_exc("_apply_offset_to_stage_absolute_if_needed failed", ex)
            return False

    def _apply_all_to_stage(self) -> bool:
        if not self._ensure_ready():
            _log("_apply_all_to_stage: ensure_ready failed")
            return False

        ok_axis = self._apply_axis_to_stage_if_needed()
        ok_off = self._apply_offset_to_stage_absolute_if_needed()
        _log(f"_apply_all_to_stage: ok_axis={ok_axis} ok_offset={ok_off}")
        return bool(ok_axis and ok_off)

    # ---------- external state API ----------
    def set_enabled(self, enabled: bool) -> Dict[str, Any]:
        self._enabled = bool(enabled)
        _log(f"set_enabled({self._enabled})")
        self._settings.set(SETTING_SECTION_ENABLED, self._enabled)
        if self._enabled:
            self._settings.set(SETTING_SECTION_ALWAYS_DISPLAY, True)

        # enabled 켰으면 한번은 적용되게 dirty
        self._dirty_axis = True
        self._dirty_offset = True
        return self.get_state()

    def set_axis(self, axis: str) -> Dict[str, Any]:
        axis = (axis or "").upper()
        if axis not in self.AXES:
            raise ValueError(f"axis must be one of {self.AXES}")
        self._axis = axis
        _log(f"set_axis({self._axis})")

        # axis 변경 -> align & base 재캡처 필요
        self._dirty_axis = True
        self._dirty_offset = True
        self._base_world_pos = None
        return self.get_state()

    def set_flip(self, flip: bool) -> Dict[str, Any]:
        """
        ✅ flip은 '절단 방향'만 변경
        ✅ 절단면 위치(offset)는 그대로 유지
        """
        self._flip = bool(flip)
        direction = 0 if self._flip else 1
        _log(f"set_flip({self._flip}) direction={direction}")
        self._settings.set(SETTING_SECTION_DIRECTION, direction)

        # ✅ 핵심 변경: flip은 offset 위치에 영향이 없으므로 offset 재적용(dirty) 하지 않음
        return self.get_state()

    def set_offset(self, offset: float) -> Dict[str, Any]:
        self._offset = float(offset)
        _log(f"set_offset({self._offset})")

        # offset 변경 -> offset 재적용 필요
        self._dirty_offset = True
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
            "base_world_pos": tuple(self._base_world_pos) if self._base_world_pos else None,
            "applied_axis": self._applied_axis,
            "applied_signed_offset": self._applied_signed_offset,
            "dirty_axis": self._dirty_axis,
            "dirty_offset": self._dirty_offset,
        }

    def apply_once_if_possible(self, attempt: int) -> bool:
        _log(f"apply_once attempt={attempt} enabled={self._enabled} stage_ready={self.is_stage_ready()}")
        if not self._enabled:
            _log("apply_once: disabled -> skip apply (DONE)")
            return True

        # ✅ 매 tick마다 무조건 적용하지 않고, dirty일 때만 반영
        if not (self._dirty_axis or self._dirty_offset):
            return True

        return self._apply_all_to_stage()
