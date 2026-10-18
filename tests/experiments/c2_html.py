"""Compatibility import; implementation lives in app.pipeline.html_renderer."""

import sys
from app.pipeline import html_renderer as _implementation

sys.modules[__name__] = _implementation
