"""Compatibility import; implementation lives in app.pipeline.service."""

import sys
from app.pipeline import service as _implementation

sys.modules[__name__] = _implementation
