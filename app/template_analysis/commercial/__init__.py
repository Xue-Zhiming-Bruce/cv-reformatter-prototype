"""Provider-neutral commercial layout evidence and production adapters."""

from app.template_analysis.commercial.adobe import (
    AdobeAdapterError,
    AdobeConfigurationError,
    normalize_adobe_layout,
    run_adobe_layout,
)
from app.template_analysis.commercial.models import (
    MeasurementProvenance,
    NormalizedBadgeCluster,
    NormalizedBox,
    NormalizedLayoutEvidence,
    NormalizedPage,
    NormalizedRule,
    NormalizedTextBlock,
)
from app.template_analysis.commercial.bridge import (
    CompileBridgeEvidenceError,
    build_design_evidence_from_normalized,
    derive_heading_candidates,
)
from app.template_analysis.commercial.local_color import (
    enrich_badges_from_local_pdf,
    enrich_colors_from_local_pdf,
    enrich_rules_from_local_pdf,
)

__all__ = [
    "AdobeAdapterError",
    "AdobeConfigurationError",
    "CompileBridgeEvidenceError",
    "MeasurementProvenance",
    "NormalizedBadgeCluster",
    "NormalizedBox",
    "NormalizedLayoutEvidence",
    "NormalizedPage",
    "NormalizedRule",
    "NormalizedTextBlock",
    "normalize_adobe_layout",
    "build_design_evidence_from_normalized",
    "derive_heading_candidates",
    "enrich_badges_from_local_pdf",
    "enrich_colors_from_local_pdf",
    "enrich_rules_from_local_pdf",
    "run_adobe_layout",
]
