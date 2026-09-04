"""Provider-neutral layout-proof contracts.

A layout proof is a temporary synthetic render of a validated
``LayoutTemplateSpec`` using only clearly synthetic placeholder content. It
exists so a recruiter can confirm that a target layout reflows safely before
any real ``CandidateProfile`` data is bound to it.

These models describe the proof workflow only:

- ``LengthVariant``: short / medium / long synthetic content variants;
- ``SyntheticPlaceholderRenderContext``: render-context-shaped data whose
  every textual value is validated against a strict synthetic-placeholder
  policy (``SYNTHETIC_*`` markers, safe reserved domains, no real names,
  emails, phones, employers, URLs, or reserved disclosure phrases);
- ``LayoutProofRenderRequest`` / ``LayoutProofRenderResult``: the artifact-
  scoped proof render contract including proof HTML/PDF sha256 checksums;
- ``StructuralValidationResult``: deterministic per-page geometry/structure
  checks;
- ``VisualComparisonEvidence``: deterministic comparison evidence against the
  target (reusing the existing ``VisualComparisonReport``) plus an optional,
  clearly advisory LLM note;
- ``VariantProofEvidence``: immutable evidence for one content variant,
  cryptographically bound to the artifact, target, layout spec, and the proof
  HTML/PDF files;
- ``LayoutProofApproval``: an approval aggregate requiring successful evidence
  for all three ``LengthVariant`` values; the model refuses an ``approved``
  state when structural checks are absent or failed, any variant is missing or
  inconsistent, or approval metadata is incomplete.

Candidate content and target presentation stay strictly separate: these
models reference ``LayoutTemplateSpec`` (presentation) and carry only
synthetic content values.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.generation.template_mapper import (
    ClientFacingRenderContext,
    RenderDetail,
    RenderEducation,
    RenderWorkExperience,
)
from app.template_analysis.schemas import (
    LayoutTemplateSpec,
    TemplateStyleSpec,
    VisualComparisonReport,
)


# Versioned schema identifiers. Bump intentionally; never reuse a version.
# render/2 + render/result/4: artifact-scoped render contract with proof file
# checksums. approval/2: evidence-bound aggregate over all three variants.
LAYOUT_PROOF_CONTEXT_SCHEMA_VERSION = "layout/proof/context/1"
LAYOUT_PROOF_RENDER_SCHEMA_VERSION = "layout/proof/render/2"
LAYOUT_PROOF_RENDER_RESULT_SCHEMA_VERSION = "layout/proof/render/result/4"
LAYOUT_PROOF_STRUCTURAL_SCHEMA_VERSION = "layout/proof/structural/1"
LAYOUT_PROOF_VISUAL_SCHEMA_VERSION = "layout/proof/visual/1"
LAYOUT_PROOF_VARIANT_SCHEMA_VERSION = "layout/proof/variant/1"
LAYOUT_PROOF_APPROVAL_SCHEMA_VERSION = "layout/proof/approval/2"


class LayoutProofModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LengthVariant(str, Enum):
    SHORT = "short"
    MEDIUM = "medium"
    LONG = "long"


#: Reserved client-facing disclosure phrases that must never appear in proof
#: content; they belong to explicit recruiter disclosure choices only.
RESERVED_DISCLOSURE_PHRASES = ("to be confirmed", "available upon request")

#: Allowlisted grammar for synthetic proof content (replaces substring
#: matching). A value is synthetic only when it is exactly one or more
#: ``SYNTHETIC_*`` tokens (optionally joined by `` | ``, `` - ``, or a single
#: space), an explicitly allowed contact line (fixed presentation label plus a
#: synthetic token or an approved ``example.invalid`` / synthetic URL), or an
#: allowlisted exact value such as the proof template name. Mixed real/
#: synthetic values, ordinary email domains, arbitrary URLs, and the reserved
#: disclosure phrases are rejected.
_SYNTHETIC_TOKEN = re.compile(r"^SYNTHETIC_[A-Z0-9_]+$")
_SYNTHETIC_LIST = re.compile(
    r"^SYNTHETIC_[A-Z0-9_]+(( \| | - | )SYNTHETIC_[A-Z0-9_]+)*$"
)
_CONTACT_LINE = re.compile(
    r"^(Location|Email|Phone|LinkedIn|Portfolio): "
    r"(SYNTHETIC_[A-Z0-9_]+"
    r"|synthetic\.candidate@example\.invalid"
    r"|https://www\.linkedin\.com/in/synthetic-candidate-1"
    r"|https://synthetic\.example\.invalid/portfolio)$"
)
_ALLOWED_EXACT_VALUES = frozenset({"layout_proof_synthetic"})


def is_synthetic_proof_text(value: str) -> bool:
    """Strict allowlisted synthetic-placeholder policy for a proof string.

    Permits empty values, exact ``SYNTHETIC_*`` tokens (and token lists joined
    by `` | ``, `` - ``, or a single space), explicitly allowed contact lines
    (``Location:``/``Email:``/``Phone:``/``LinkedIn:``/``Portfolio:`` followed
    by a synthetic token or an approved ``example.invalid``/synthetic URL), and
    the allowlisted proof template name. Rejects mixed real/synthetic values,
    ordinary email domains, arbitrary URLs, and the reserved disclosure
    phrases — never by substring matching.
    """
    stripped = value.strip()
    if not stripped:
        return True
    lowered = stripped.lower()
    if any(phrase in lowered for phrase in RESERVED_DISCLOSURE_PHRASES):
        return False
    if _SYNTHETIC_TOKEN.match(stripped):
        return True
    if _SYNTHETIC_LIST.match(stripped):
        return True
    if _CONTACT_LINE.match(stripped):
        return True
    return stripped in _ALLOWED_EXACT_VALUES


def non_synthetic_strings(payload: object) -> list[str]:
    """Recursively collect string values that violate the synthetic policy."""

    def walk(node: object) -> None:
        if isinstance(node, str):
            if not is_synthetic_proof_text(node):
                offending.append(node)
        elif isinstance(node, dict):
            for child in node.values():
                walk(child)
        elif isinstance(node, (list, tuple)):
            for child in node:
                walk(child)

    offending: list[str] = []
    walk(payload)
    return offending


class SyntheticPlaceholderRenderContext(LayoutProofModel):
    """Render-context-shaped data containing only validated synthetic values.

    Mirrors the shape of ``ClientFacingRenderContext`` so the existing
    rendering pipeline can consume it unchanged. Every textual value is
    validated against the strict synthetic-placeholder policy; real
    candidate, target-sample, or disclosure content is rejected at
    construction time.
    """

    schema_version: Literal["layout/proof/context/1"] = LAYOUT_PROOF_CONTEXT_SCHEMA_VERSION
    variant: LengthVariant
    template_name: str = "layout_proof_synthetic"
    candidate_heading: str
    candidate_subheading: str | None = None
    contact_lines: list[str] = Field(default_factory=list)
    professional_summary: str | None = None
    skills: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    work_experience: list[RenderWorkExperience] = Field(default_factory=list)
    education: list[RenderEducation] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    additional_details: list[RenderDetail] = Field(default_factory=list)
    blind_profile: bool = False

    @model_validator(mode="after")
    def _only_synthetic_content(self) -> "SyntheticPlaceholderRenderContext":
        offending = non_synthetic_strings(
            self.model_dump(mode="json", exclude={"schema_version", "variant"})
        )
        if offending:
            raise ValueError(
                "SyntheticPlaceholderRenderContext contains non-synthetic text: "
                + repr(offending[:5])
            )
        return self

    def to_render_context(self) -> ClientFacingRenderContext:
        """Convert into the existing client-facing render contract.

        The existing renderer consumes ``ClientFacingRenderContext``; this is
        the only bridge from synthetic proof content into the pipeline. The
        proof-only fields (``schema_version``, ``variant``) are excluded so
        strict validation of the render contract succeeds.
        """
        return ClientFacingRenderContext.model_validate(
            self.model_dump(mode="json", exclude={"schema_version", "variant"})
        )


class LayoutProofRenderRequest(LayoutProofModel):
    """Artifact-scoped request to render a proof for one content variant.

    Contains only presentation references (the provider-neutral template
    spec — a compiled v2 ``LayoutTemplateSpec`` or the accepted v1
    ``TemplateStyleSpec``), the owning artifact, and the target reference.
    Renderer-specific details never belong here.
    """

    schema_version: Literal["layout/proof/render/2"] = LAYOUT_PROOF_RENDER_SCHEMA_VERSION
    artifact_id: str
    # Keep the more specific v2 subtype first. Pydantic otherwise accepts a
    # LayoutTemplateSpec as its v1 base class and drops the compiled sections,
    # making proof rendering validate a different template than compilation.
    layout_spec: LayoutTemplateSpec | TemplateStyleSpec
    variant: LengthVariant
    target_checksum: str


class LayoutProofRenderResult(LayoutProofModel):
    """Result of a successfully rendered layout proof.

    Records filenames, proof HTML/PDF sha256 checksums, and measured page
    geometry so structural validation and approval can work from immutable
    deterministic facts only.
    """

    schema_version: Literal["layout/proof/render/result/4"] = LAYOUT_PROOF_RENDER_RESULT_SCHEMA_VERSION
    proof_id: str
    artifact_id: str
    variant: LengthVariant
    layout_spec_checksum: str
    target_checksum: str
    context_checksum: str
    html_filename: str
    pdf_filename: str
    html_checksum: str
    pdf_checksum: str
    page_count: int | None = Field(default=None, ge=0)


class StructuralCheck(LayoutProofModel):
    name: str
    passed: bool
    detail: str = ""


class StructuralValidationResult(LayoutProofModel):
    """Deterministic geometry/structure check results for a rendered proof.

    ``passed`` must be False whenever any individual check failed, so a
    failed proof can never be represented as valid.
    """

    schema_version: Literal["layout/proof/structural/1"] = LAYOUT_PROOF_STRUCTURAL_SCHEMA_VERSION
    passed: bool
    page_count: int = Field(ge=0)
    checks: list[StructuralCheck] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent_passed(self) -> "StructuralValidationResult":
        if self.passed and any(not check.passed for check in self.checks):
            raise ValueError(
                "StructuralValidationResult.passed must be False when any check fails"
            )
        return self


class VisualComparisonEvidence(LayoutProofModel):
    """Deterministic visual-comparison evidence for a rendered layout proof.

    Measurements live in the existing ``VisualComparisonReport``
    (deterministic pixel/structure comparison). ``llm_advisory`` is optional
    and strictly advisory: it never supplies measurements or final approval.
    """

    schema_version: Literal["layout/proof/visual/1"] = LAYOUT_PROOF_VISUAL_SCHEMA_VERSION
    report: VisualComparisonReport
    llm_advisory: str | None = None


class VariantProofEvidence(LayoutProofModel):
    """Immutable evidence for one content variant's layout proof.

    The embedded ``render_result`` cryptographically binds the artifact,
    target, layout spec, and the proof HTML/PDF files (sha256 checksums).
    The structural result must match that exact rendered PDF's page count,
    and the visual evidence is required deterministic comparison evidence.
    """

    schema_version: Literal["layout/proof/variant/1"] = LAYOUT_PROOF_VARIANT_SCHEMA_VERSION
    variant: LengthVariant
    render_result: LayoutProofRenderResult
    structural: StructuralValidationResult
    visual: VisualComparisonEvidence

    @model_validator(mode="after")
    def _consistent_evidence(self) -> "VariantProofEvidence":
        if self.render_result.variant is not self.variant:
            raise ValueError(
                "render result variant does not match evidence variant"
            )
        if self.structural.page_count != (self.render_result.page_count or 0):
            raise ValueError(
                "structural result page count does not match the render result"
            )
        return self


class LayoutProofApproval(LayoutProofModel):
    """Explicit approval aggregate over all three content-length variants.

    ``state`` defaults to ``pending``. An ``approved`` record requires
    complete, consistent evidence for every ``LengthVariant`` plus explicit
    approval metadata (``approved_at``, ``reviewer_id``); the model rejects
    any attempt to approve when structural checks are absent or failed, any
    variant is missing or inconsistent with the artifact/target/layout
    checksums, or approval metadata is incomplete.
    """

    schema_version: Literal["layout/proof/approval/2"] = LAYOUT_PROOF_APPROVAL_SCHEMA_VERSION
    approval_id: str
    artifact_id: str
    target_checksum: str
    layout_spec_checksum: str
    state: Literal["pending", "approved", "rejected"] = "pending"
    variants: dict[LengthVariant, VariantProofEvidence]
    approved_at: str | None = None
    reviewer_id: str | None = None
    reviewer_note: str | None = None

    @model_validator(mode="after")
    def _all_required_variants_present(self) -> "LayoutProofApproval":
        missing = sorted(
            set(LengthVariant) - set(self.variants),
            key=lambda variant: variant.value,
        )
        if missing:
            raise ValueError(
                "approval requires evidence for all three LengthVariant values; "
                f"missing: {[variant.value for variant in missing]}"
            )
        return self

    @model_validator(mode="after")
    def _consistent_scope(self) -> "LayoutProofApproval":
        for variant, evidence in self.variants.items():
            if evidence.variant is not variant:
                raise ValueError(
                    f"evidence key '{variant.value}' does not match "
                    f"evidence.variant '{evidence.variant.value}'"
                )
            if evidence.render_result.artifact_id != self.artifact_id:
                raise ValueError(
                    f"{variant.value}: artifact mismatch "
                    f"({evidence.render_result.artifact_id} != {self.artifact_id})"
                )
            if evidence.render_result.layout_spec_checksum != self.layout_spec_checksum:
                raise ValueError(f"{variant.value}: layout spec checksum mismatch")
            if evidence.render_result.target_checksum != self.target_checksum:
                raise ValueError(f"{variant.value}: target checksum mismatch")
        return self

    @model_validator(mode="after")
    def _approval_invariants(self) -> "LayoutProofApproval":
        if self.state == "approved":
            if not self.approved_at or not self.reviewer_id:
                raise ValueError(
                    "approved approval requires approved_at and reviewer_id"
                )
            for variant, evidence in self.variants.items():
                if not evidence.structural.checks:
                    raise ValueError(
                        f"{variant.value}: no deterministic structural checks recorded"
                    )
                if not evidence.structural.passed:
                    raise ValueError(
                        f"{variant.value}: structural validation failed"
                    )
                if evidence.visual is None:
                    raise ValueError(f"{variant.value}: visual evidence is absent")
        return self

    @property
    def is_approved(self) -> bool:
        return self.state == "approved"
