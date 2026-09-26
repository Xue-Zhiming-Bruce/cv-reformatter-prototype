"""Text-normalisation primitives shared by every layer of the library.

``_norm`` was split out of ``c2_renderer`` (the delivery-gate module) because it
is a leaf operation, and importing it from the gate module closed an import
cycle:

    docx_lane.build -> c2_renderer -> resume_layout.html
                    -> resume_layout.plan -> docx_lane.build

This module imports nothing from ``tests.experiments`` and must stay that way so
that anything may depend on it.
"""
from __future__ import annotations

import re


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace(":", " ")).strip().casefold()
