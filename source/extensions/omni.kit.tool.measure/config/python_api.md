# Public API for module omni.kit.tool.measure:

## Classes

- class Extension(omni.ext.IExt)
  - [property] def panel(self) -> Optional[MeasurePanel]
  - [property] def viewport(self) -> Optional[MeasureScene]
  - def on_startup(self, ext_id)
  - def on_shutdown(self)

## Functions

- def get_instance() -> Extension
