# ---------------------------------------------------------------------
# core.py  (morph/section_control/core.py)
# ---------------------------------------------------------------------
import time
import traceback
from typing import Optional, Dict, Any

import carb
import carb.settings
import omni.usd

from pxr import Gf, Usd, UsdGeom, Sdf
from omni.kit.window.section.common import SectionManager
from omni.kit.window.section.common import (
    SETTING_SECTION_ENABLED,
    SETTING_SECTION_DIRECTION,
    SETTING_SECTION_ALWAYS_DISPLAY,
    SETTING_SECTION_LIGHT,
    # ✅ 추가: manipulator 표시 토글(= Display Section Manipulator)
    SETTING_SECTION_MANIPULATOR,
    # ✅ 추가: RTX 기본 manipulator 토글(버전에 따라 UI 초기값 영향)
    SETTING_RTX_DEFAULT_SECTION_MANIPULATOR,
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


class SectionController:
    AXES = ("X", "Y", "Z")
    USE_SAMPLE_AXIS_MAPPING = False

    def __init__(self):
        self._settings = carb.settings.get_settings()

        self._sec_mgr: Optional[SectionManager] = None
        self._widget_path: Optional[Sdf.Path] = None
        self._last_stage_id: Optional[int] = None

        self._enabled = bool(self._settings.get(SETTING_SECTION_ENABLED) or False)
        self._axis = "X"
        self._flip = bool(self._settings.get(SETTING_SECTION_DIRECTION) == 0)  # 0=flip, 1=normal (프로젝트 가정 유지)
        self._offset = 0.0

        self._always_display = bool(self._settings.get(SETTING_SECTION_ALWAYS_DISPLAY) or False)
        self._light = bool(self._settings.get(SETTING_SECTION_LIGHT) or False)

        self._base_world_pos: Optional[Gf.Vec3d] = None
        self._applied_axis: Optional[str] = None
        self._applied_signed_offset: float = 0.0

        self._dirty_axis: bool = True
        self._dirty_offset: bool = True

        # ✅ 시작 시점에도 강제로 OFF (UI 초기 토글이 ON인 문제를 여기서 해결)
        self._force_section_manipulator_off()

    # -------------------- ✅ 핵심: manipulator 토글 OFF --------------------
    def _force_section_manipulator_off(self) -> None:
        """
        UI의 'Display Section Manipulator' 토글과 동일한 setting을 강제로 OFF.
        - SETTING_SECTION_MANIPULATOR
        - SETTING_RTX_DEFAULT_SECTION_MANIPULATOR (RTX 환경에서 초기값에 영향)
        """
        try:
            self._settings.set(SETTING_SECTION_MANIPULATOR, False)
        except Exception:
            pass

        try:
            self._settings.set(SETTING_RTX_DEFAULT_SECTION_MANIPULATOR, False)
        except Exception:
            pass

    # ---------------- stage helpers ----------------
    def _get_stage(self) -> Optional[Usd.Stage]:
        return omni.usd.get_context().get_stage()

    def _stage_identity(self, stage: Optional[Usd.Stage]) -> Optional[int]:
        return None if stage is None else id(stage)

    def is_stage_ready(self) -> bool:
        return self._get_stage() is not None

    def invalidate(self, reason: str):
        self._sec_mgr = None
        self._widget_path = None
        self._last_stage_id = None

        self._base_world_pos = None
        self._applied_axis = None
        self._applied_signed_offset = 0.0
        self._dirty_axis = True
        self._dirty_offset = True

        # ✅ stage swap 후에도 다시 OFF 보장
        self._force_section_manipulator_off()

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
            return False

        stage_id = self._stage_identity(stage)
        if self._last_stage_id is None:
            self._last_stage_id = stage_id
        elif stage_id != self._last_stage_id:
            self.invalidate("stage_swapped")
            self._last_stage_id = stage_id

        if self._sec_mgr is None:
            self._sec_mgr = SectionManager()

        if self._widget_path is None:
            try:
                w = self._sec_mgr.get_section_widget_prim(True)
                if isinstance(w, Usd.Prim):
                    self._widget_path = w.GetPath()
                elif isinstance(w, Sdf.Path):
                    self._widget_path = w
                else:
                    self._widget_path = Sdf.Path(str(w))
            except Exception:
                return False

        return True

    def _with_section_edit_context(self):
        class _NoOpCtx:
            def __enter__(self): return self
            def __exit__(self, exc_type, exc, tb): return False

        if self._sec_mgr and hasattr(self._sec_mgr, "_get_section_edit_context"):
            try:
                return self._sec_mgr._get_section_edit_context()
            except Exception:
                pass
        return _NoOpCtx()

    def _get_widget_prim(self) -> Optional[Usd.Prim]:
        if not self._ensure_ready():
            return None
        stage = self._get_stage()
        if stage is None or self._widget_path is None:
            return None
        prim = stage.GetPrimAtPath(self._widget_path)
        if not prim or not prim.IsValid():
            return None
        return prim

    def _get_widget_world_translation(self, prim: Usd.Prim) -> Gf.Vec3d:
        xform = UsdGeom.Xformable(prim)
        m = xform.ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        t = m.ExtractTranslation()
        return Gf.Vec3d(t[0], t[1], t[2])

    # ---------------- apply ----------------
    def _apply_axis_to_stage_if_needed(self) -> bool:
        if not self._ensure_ready():
            return False

        # ✅ 매 apply에서 OFF 재보장 (다른 쪽이 건드려도 다시 꺼짐)
        self._force_section_manipulator_off()

        if not self._dirty_axis and self._applied_axis == self._axis:
            return True

        arg = self._axis_to_align_arg(self._axis)
        with self._with_section_edit_context():
            self._sec_mgr.align_widget(arg)

        prim = self._get_widget_prim()
        self._base_world_pos = self._get_widget_world_translation(prim) if prim else None

        self._applied_axis = self._axis
        self._applied_signed_offset = 0.0
        self._dirty_axis = False
        self._dirty_offset = True
        return True

    def _apply_offset_to_stage_absolute_if_needed(self) -> bool:
        if not self._ensure_ready() or not self._dirty_offset:
            # ✅ offset 변경 없어도 OFF 재보장
            self._force_section_manipulator_off()
            return True

        prim = self._get_widget_prim()
        if prim is None:
            return False

        signed_offset = float(self._offset)

        if self._base_world_pos is None:
            cur = self._get_widget_world_translation(prim)
            base = Gf.Vec3d(cur[0], cur[1], cur[2])
            if self._applied_signed_offset != 0.0:
                if self._axis == "X":
                    base[0] -= self._applied_signed_offset
                elif self._axis == "Y":
                    base[1] -= self._applied_signed_offset
                else:
                    base[2] -= self._applied_signed_offset
            self._base_world_pos = base

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

        # ✅ 위치 적용 후에도 OFF 재보장
        self._force_section_manipulator_off()
        return True

    def _apply_all_to_stage(self) -> bool:
        if not self._ensure_ready():
            return False
        ok = bool(self._apply_axis_to_stage_if_needed() and self._apply_offset_to_stage_absolute_if_needed())
        # ✅ 최종적으로도 OFF 보장
        self._force_section_manipulator_off()
        return ok

    # ---------------- external API ----------------
    def set_enabled(self, enabled: bool) -> Dict[str, Any]:
        self._enabled = bool(enabled)
        self._settings.set(SETTING_SECTION_ENABLED, self._enabled)

        if self._enabled:
            self._settings.set(SETTING_SECTION_ALWAYS_DISPLAY, True)
            # ✅ enable 켜는 순간에도 manipulator OFF 강제
            self._force_section_manipulator_off()

        self._dirty_axis = True
        self._dirty_offset = True
        return self.get_state()

    def set_axis(self, axis: str) -> Dict[str, Any]:
        axis = (axis or "").upper()
        if axis not in self.AXES:
            raise ValueError(f"axis must be one of {self.AXES}")
        self._axis = axis
        self._dirty_axis = True
        self._dirty_offset = True
        self._base_world_pos = None
        return self.get_state()

    def set_flip(self, flip: bool) -> Dict[str, Any]:
        self._flip = bool(flip)
        direction = 0 if self._flip else 1
        self._settings.set(SETTING_SECTION_DIRECTION, direction)
        return self.get_state()

    def set_offset(self, offset: float) -> Dict[str, Any]:
        self._offset = float(offset)
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
        if not self._enabled:
            self._force_section_manipulator_off()
            return True

        # ✅ ready/적용 전에 항상 OFF
        self._force_section_manipulator_off()

        if not (self._dirty_axis or self._dirty_offset):
            return True

        return self._apply_all_to_stage()
