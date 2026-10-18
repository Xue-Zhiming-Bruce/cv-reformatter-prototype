"""Compatibility import; implementation lives in app.pipeline.authored_template."""

import sys
from app.pipeline import authored_template as _implementation

sys.modules[__name__] = _implementation
