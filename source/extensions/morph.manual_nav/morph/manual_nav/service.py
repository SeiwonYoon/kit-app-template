from omni.kit.tool.teleport import Teleport

class ManualNavService:
    def __init__(self):
        self._teleport = Teleport.get_instance()

    def teleport_on(self, reason: str = "external_on") -> dict:
        self._teleport.set_active(True)
        return {"enabled": True, "reason": reason}

    def teleport_off(self, reason: str = "external_off") -> dict:
        self._teleport.set_active(False)
        return {"enabled": False, "reason": reason}

    def get_state(self) -> dict:
        _active = self._teleport.get_active()
        return {"enabled": _active}


def get_service():
    from .core import get_singleton_service
    return get_singleton_service()