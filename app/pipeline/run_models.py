"""Compatibility-bound data contracts for the E5 loop state.

These are stored E5 contract shapes: serialized schema, type, and status names
are read back by existing run records, so they move with the coordinator and
never with a provider, renderer, or product schema.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from app.pipeline.evidence import EvidenceModel


class E5LaneASection(EvidenceModel):
    """Lane A reusable primitives for ONE section (provider-neutral; the
    fixed renderer interprets them). No target identifiers, no coordinates
    outside the declared rail width, no candidate text."""

    section_node_id: str
    heading_in_rail: bool = False
    rail_label_width_pt: float | None = Field(default=None, ge=20.0, le=400.0)
    rail_label_align: Literal["left", "right"] | None = None
    entry_meta_placement: Literal["title_row"] | None = None
    @model_validator(mode="after")
    def rail_fields_complete(self) -> "E5LaneASection":
        if self.heading_in_rail and not self.rail_label_width_pt:
            raise ValueError("heading_in_rail requires rail_label_width_pt")
        if not self.heading_in_rail and (self.rail_label_width_pt or self.rail_label_align):
            raise ValueError("rail width/align require heading_in_rail")
        return self

class LaneAStructureProposal(EvidenceModel):
    """The Lane A Builder's typed structure proposal (validated; unknown or
    invalid fields fail validation at construction — `extra="forbid"`)."""

    proposal_id: str = Field(pattern=r"^[a-z0-9_-]+$")
    sections: list[E5LaneASection]
    header_contact_labels: bool | None = Field(default=None, strict=True)
    agent: Literal["scripted", "llm"]

class PresentationLabelApproval(EvidenceModel):
    """OWNER APPROVAL INPUT for ONE proposed presentation-label catalog of
    ONE target (owner-approved mechanism 2026-09-22; the owner has NOT yet
    approved any concrete label). Approval is identity-bound, never inferred:

    - ``target_sha256`` must equal the SHA-256 of the run's exact target PDF;
    - ``catalog_sha256`` must equal the canonical identity hash of the
      evidence-derived proposed catalog (label_id/text/kind/evidence_ids);
    - ``approved_label_ids`` must be non-empty, unique, and every id must
      exist in that proposed catalog.

    The model carries NO text, kind, or evidence: approval can never submit
    or override label content. The same wording in another target yields a
    different target/catalog identity, so approval cannot be inherited."""

    target_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    catalog_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    approved_label_ids: list[str]

    @model_validator(mode="after")
    def ids_nonempty_and_unique(self) -> "PresentationLabelApproval":
        if not self.approved_label_ids:
            raise ValueError("presentation-label approval requires at least one label id")
        if any(not label_id.strip() for label_id in self.approved_label_ids):
            raise ValueError("presentation-label approval label ids must be non-empty")
        if len(set(self.approved_label_ids)) != len(self.approved_label_ids):
            raise ValueError("presentation-label approval label ids must be unique")
        return self
