"""Canonical filesystem anchors for the experiments package.

Split out of ``a_pipeline`` so that every other module can depend on the two
paths without pulling in the Adobe/render machinery. Depth-independent on
purpose: the previous definition used ``Path(__file__).with_name("runs")``,
which silently pointed at the *new* module's directory after a move.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


RUNS = Path(__file__).resolve().parents[1] / "runs"
