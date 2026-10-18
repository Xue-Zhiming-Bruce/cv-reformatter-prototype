"""Compatibility import; implementation lives in app.pipeline.layout_state."""

import sys
from app.pipeline import layout_state as _implementation

sys.modules[__name__] = _implementation
