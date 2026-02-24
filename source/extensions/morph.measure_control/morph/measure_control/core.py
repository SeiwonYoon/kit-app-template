from typing import Any, Dict


class MeasureController:
    """
    In-memory controller scaffold for morph.measure_control.
    No section backend or USD stage side effects are implemented.
    """

    AXES = ("X", "Y", "Z")

    def __init__(self):
        self._enabled = False
        self._axis = "X"
        self._flip = False
        self._offset = 0.0

        self._dirty_axis = False
        self._dirty_offset = False
        self._last_apply_attempt = 0

    def set_enabled(self, enabled: bool) -> Dict[str, Any]:
        self._enabled = bool(enabled)
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
        return self.get_state()

    def set_flip(self, flip: bool) -> Dict[str, Any]:
        self._flip = bool(flip)
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
            "stage_ready": False,
            "backend_ready": False,
            "dirty_axis": self._dirty_axis,
            "dirty_offset": self._dirty_offset,
            "last_apply_attempt": self._last_apply_attempt,
        }

    def apply_once_if_possible(self, attempt: int) -> bool:
        # Placeholder apply for scaffold only.
        self._last_apply_attempt = int(attempt)
        self._dirty_axis = False
        self._dirty_offset = False
        return True
