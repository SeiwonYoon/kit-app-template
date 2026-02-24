from pathlib import Path
from typing import Dict, List
import importlib
import carb
import omni.kit.app
import omni.usd
from pxr import UsdGeom


class MeasureControlService:
    """Service for running mesh bbox measurement by prim path."""

    def __init__(self):
        self._ensured_measure_backend_once = False
        self._last_message = ""

    def startup(self):
        self.ensure_measure_backend_running(force=True)

    def shutdown(self):
        pass

    def _log(self, msg: str):
        self._last_message = msg
        carb.log_info(f"[measure_control] {msg}")

    @staticmethod
    def _local_measure_extension_path() -> Path:
        # .../source/extensions/morph.measure_control/morph/measure_control/service.py
        # -> .../source/extensions/omni.kit.tool.measure
        return Path(__file__).resolve().parents[3] / "omni.kit.tool.measure"

    def _fail(self, msg: str) -> Dict[str, object]:
        self._log(msg)
        return {"ok": False, "message": msg}

    @staticmethod
    def _extract_ext_ids(exts):
        if isinstance(exts, dict):
            return list(exts.keys())
        if isinstance(exts, (list, tuple)):
            out = []
            for item in exts:
                if isinstance(item, str):
                    out.append(item)
                elif isinstance(item, (list, tuple)) and item and isinstance(item[0], str):
                    out.append(item[0])
            return out
        return []

    def ensure_measure_backend_running(self, force: bool = False) -> bool:
        if self._ensured_measure_backend_once and not force:
            return True

        try:
            app = omni.kit.app.get_app()
            em = app.get_extension_manager()
            all_exts = self._extract_ext_ids(em.get_extensions())
        except Exception as ex:
            self._log(f"ensure_backend failed to access extension manager: {ex}")
            return False

        candidates = []
        for ext_id in all_exts:
            if not isinstance(ext_id, str):
                continue
            if ext_id == "omni.kit.tool.measure":
                candidates.append(ext_id)
            elif ext_id.startswith("omni.kit.tool.measure-"):
                candidates.append(ext_id)
            elif "omni.kit.tool.measure" in ext_id:
                candidates.append(ext_id)

        if not candidates:
            # Fallback 1: canonical extension id enable
            try:
                if hasattr(em, "set_extension_enabled_immediate"):
                    em.set_extension_enabled_immediate("omni.kit.tool.measure", True)
                else:
                    em.set_extension_enabled("omni.kit.tool.measure", True)
                self._ensured_measure_backend_once = True
                self._log("ensure_backend enabled omni.kit.tool.measure by canonical id fallback")
                return True
            except Exception as ex:
                # Fallback 2: local repo path + python import
                local_ext_path = self._local_measure_extension_path()
                if local_ext_path.exists():
                    try:
                        importlib.import_module("omni.kit.tool.measure")

                        self._ensured_measure_backend_once = True
                        self._log(f"ensure_backend resolved via local path fallback: {local_ext_path}")
                        return True
                    except Exception as import_ex:
                        self._log(
                            f"ensure_backend local fallback import failed ({local_ext_path}): {import_ex}"
                        )
                        return False

                self._log(f"ensure_backend omni.kit.tool.measure not found: {ex}")
                return False

        ok = False
        for ext_id in candidates:
            try:
                if hasattr(em, "set_extension_enabled_immediate"):
                    em.set_extension_enabled_immediate(ext_id, True)
                else:
                    em.set_extension_enabled(ext_id, True)
                ok = True
            except Exception as ex:
                self._log(f"ensure_backend enable failed for {ext_id}: {ex}")

        self._ensured_measure_backend_once = ok
        return ok

    def _has_mesh_prim(self, prim) -> bool:
        if prim.IsA(UsdGeom.Camera):
            return False
        if prim.IsA(UsdGeom.Mesh):
            return True
        for child in prim.GetChildren():
            if self._has_mesh_prim(child):
                return True
        return False

    @staticmethod
    def _is_top_level_world_prim(prim) -> bool:
        """Top-level /World prim is excluded from UI list by requirement."""
        try:
            if not prim or not prim.IsValid():
                return False
            path = prim.GetPath()
            if not path or path.pathString != "/World":
                return False
            parent = prim.GetParent()
            return bool(parent and parent.GetPath() and parent.GetPath().pathString == "/")
        except Exception:
            return False

    def get_stage_prim_items(self, max_items: int = 2000) -> List[Dict[str, object]]:
        ctx = omni.usd.get_context()
        stage = ctx.get_stage()
        if not stage:
            return []

        items: List[Dict[str, object]] = []
        count = 0
        for prim in stage.Traverse():
            if not prim or not prim.IsValid():
                continue
            if self._is_top_level_world_prim(prim):
                continue
            path = str(prim.GetPath())
            if path == "/":
                continue
            has_mesh = bool(prim.IsA(UsdGeom.Mesh) or self._has_mesh_prim(prim))
            if not has_mesh:
                continue
            items.append({"path": path, "has_mesh": has_mesh})
            count += 1
            if count >= max_items:
                break
        return items

    def measure_mesh_for_prim_path(self, prim_path: str) -> Dict[str, object]:
        path = (prim_path or "").strip()
        if not path:
            return self._fail("empty prim path")

        ctx = omni.usd.get_context()
        stage = ctx.get_stage()
        if not stage:
            return self._fail("stage is not ready")

        prim = stage.GetPrimAtPath(path)
        if not prim or not prim.IsValid():
            return self._fail(f"invalid prim path: {path}")

        if prim.IsA(UsdGeom.Camera):
            return self._fail(f"camera prim is not supported: {path}")

        if not self._has_mesh_prim(prim) and not prim.IsA(UsdGeom.Mesh):
            return self._fail(f"mesh not found under prim: {path}")

        if not self.ensure_measure_backend_running():
            return self._fail(f"omni.kit.tool.measure backend is not available ({self._last_message})")

        # Execute Measure Manager "Delete All" before creating a new mesh measurement.
        try:
            from omni.kit.tool.measure.manager import MeasurementManager

            MeasurementManager().delete_all()
        except Exception as ex:
            # Continue even if cleanup fails.
            self._log(f"delete_all before measure failed: {ex}")

        try:
            from omni.kit.tool.measure.viewport.tools.mesh import _create_bbox_axis_measurements_impl
        except Exception as ex:
            return self._fail(f"measure mesh api import failed: {ex}")

        try:
            _create_bbox_axis_measurements_impl(prim, max_depth=0)
            msg = f"mesh bbox measurement created for {path}"
            self._log(msg)
            return {"ok": True, "message": msg, "path": path}
        except Exception as ex:
            return self._fail(f"mesh measurement failed for {path}: {ex}")

    def get_state(self) -> Dict[str, object]:
        return {
            "backend_ready": bool(self._ensured_measure_backend_once),
            "last_message": self._last_message,
        }


