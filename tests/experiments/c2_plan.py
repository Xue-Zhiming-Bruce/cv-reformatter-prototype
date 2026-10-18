"""Compatibility import; implementation lives in app.pipeline.render_plan."""

import sys
from app.pipeline import render_plan as _implementation

sys.modules[__name__] = _implementation
