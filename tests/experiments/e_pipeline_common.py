"""Compatibility import; implementation lives in app.pipeline.evidence."""

import sys
from app.pipeline import evidence as _implementation

sys.modules[__name__] = _implementation
