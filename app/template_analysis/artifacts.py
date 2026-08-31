"""Shared target-analysis artifact names and validated spec loaders."""

from __future__ import annotations

import json
from pathlib import Path

from app.template_analysis.schemas import LayoutTemplateSpec, TemplateStyleSpec


STYLE_SPEC_FILENAME = "template_style_spec.json"
LAYOUT_SPEC_FILENAME = "layout_template_spec.json"
NORMALIZED_LAYOUT_FILENAME = "normalized_layout_evidence.json"
TARGET_EVIDENCE_FILENAME = "target_layout_evidence.json"
ADOBE_RAW_FILENAME = "adobe_raw_response.json"


class TargetAnalysisArtifactError(RuntimeError):
    pass


def load_template_style_spec(path: str | Path) -> TemplateStyleSpec:
    return _load(path, TemplateStyleSpec)


def load_layout_template_spec(path: str | Path) -> LayoutTemplateSpec:
    return _load(path, LayoutTemplateSpec)


def _load(path: str | Path, model):
    try:
        return model.model_validate_json(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise TargetAnalysisArtifactError(f"Target analysis artifact could not be loaded: {path}") from exc
