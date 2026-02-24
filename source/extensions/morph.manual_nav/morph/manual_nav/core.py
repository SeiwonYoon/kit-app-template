from typing import Optional

_service_instance = None

def initialize_service():
    global _service_instance
    if _service_instance is None:
        from .service import ManualNavService
        _service_instance = ManualNavService()

def shutdown_service():
    global _service_instance
    _service_instance = None

def get_singleton_service():
    global _service_instance
    return _service_instance