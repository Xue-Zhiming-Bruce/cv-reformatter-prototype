"""Presentation-label recognition and approval policy.

Pure policy over supplied target/template evidence: propose the catalog, compute
its canonical identity, and project an identity-bound owner approval. No model
calls, no rendering, and no acceptance of a generated document.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.pipeline.evidence import PresentationLabel
from app.pipeline.run_models import PresentationLabelApproval


def _presentation_label_catalog(derived: dict[str, Any]) -> list[PresentationLabel]:
    """PROPOSE presentation labels from the measured sidebar-label evidence
    that already feeds the StructureDraft and both lanes' evidence package.

    Every entry is `proposed` and stays proposed: the catalog is purely
    evidence-derived and NEVER approves anything by itself. Approval is an
    owner INPUT (`PresentationLabelApproval`) bound to this exact target and
    this exact catalog identity — not a property the shell can infer from
    text, file name, or hash. Measurement only establishes provenance, not
    label-vs-person-fact classification, so an unapproved entry must never
    become renderable or privacy-excluded.

    Deliberately minimal: ``section_heading`` is the only label type with
    reliable target structure evidence today. Contact ``field_label`` entries
    are NOT implemented — the two-column compile records those rows as a
    capability gap, so emitting them would fabricate evidence. No file-name or
    hash branch: the text is exactly what the measurement returned. A
    duplicate measured id fails closed."""
    catalog: list[PresentationLabel] = []
    seen: set[str] = set()
    for record in derived.get("sidebar_labels") or []:
        page = int(record["page"])
        top = float(record["top"])
        label_id = f"label.section.p{page}.top{top:.1f}"
        if label_id in seen:
            raise ValueError(
                f"duplicate presentation label id from the measurement: {label_id!r}"
            )
        seen.add(label_id)
        text = str(record.get("text") or "")
        catalog.append(
            PresentationLabel(
                label_id=label_id,
                text=text,
                kind="section_heading",
                evidence_ids=[f"local_pdf.sidebar_label.p{page}.top{top:.1f}"],
            )
        )
    return catalog

def _unrendered_target_headings(
    normalized: Any, template_labels: list[PresentationLabel],
) -> list[PresentationLabel]:
    """PROPOSE the conventional target headings the measured TEMPLATE does not
    render, as evidence-bound catalog entries.

    A target also uses conventional section headings the template chose not to
    reproduce (e.g. a ``SUMMARY`` heading above a summary paragraph rendered
    without a heading). Such a target line is not a leaked candidate fact, but
    admitting it to the privacy exclusion set is an owner decision, so these
    entries stay `proposed`: never renderable, never privacy-excluded, and
    admitted only by an explicit `PresentationLabelApproval` bound to this
    exact target and catalog. Exact product-vocabulary aliases only, so body
    text and target-person facts never become labels here."""
    from app.pipeline.quality import _norm
    from app.template_analysis.commercial.bridge import _section_source_hint

    rendered = {_norm(label.text) for label in template_labels}
    labels: list[PresentationLabel] = []
    seen: set[str] = set()
    for block in getattr(normalized, "text_blocks", None) or []:
        text = " ".join((block.text or "").split())
        key = _norm(text)
        if not key or key in rendered or key in seen:
            continue
        if _section_source_hint(text)[0] == "additional_section":
            continue
        seen.add(key)
        labels.append(PresentationLabel(
            label_id=f"label.target.p{block.page_number}.{block.element_id}",
            text=text,
            kind="section_heading",
            evidence_ids=[f"local_pdf.{block.element_id}"],
        ))
    return labels

def template_presentation_label_catalog(
    state: Any, normalized: Any, *, target_sha256: str,
) -> dict[str, Any]:
    """The exact presentation-label identity a template-driven run must approve.

    ``labels`` is what the measured template renders plus the target headings it
    does not render, and ``catalog_sha256`` is its canonical identity; ``run_e5``
    computes the same values, so an owner approving this catalog is approving
    exactly the identity the run validates.
    """
    template_labels = [
        PresentationLabel(label_id=f"label.template.{node.node_id}", text=node.label,
                          kind="section_heading", evidence_ids=node.evidence_ids)
        for node in state.nodes if node.kind == "heading"
    ]
    labels = [*template_labels, *_unrendered_target_headings(normalized, template_labels)]
    recognized = {label.label_id for label in _heuristic_presentation_labels(template_labels)}
    return {
        "target_sha256": target_sha256,
        "catalog_sha256": presentation_label_catalog_sha256(labels),
        "labels": labels,
        "unrecognized_template_ids": sorted(
            {label.label_id for label in template_labels} - recognized
        ),
    }


def presentation_label_catalog_sha256(catalog: list[PresentationLabel]) -> str:
    """Deterministic identity of the EVIDENCE-DERIVED proposed catalog:
    canonical JSON (labels sorted by label_id, object keys sorted, UTF-8) over
    label_id / text / kind / evidence_ids. `status` is deliberately EXCLUDED:
    status comes from the owner approval, not from the evidence."""
    payload = [
        {
            "label_id": label.label_id,
            "text": label.text,
            "kind": label.kind,
            "evidence_ids": list(label.evidence_ids),
        }
        for label in sorted(catalog, key=lambda label: label.label_id)
    ]
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()

def _apply_presentation_label_approval(
    catalog: list[PresentationLabel],
    approval: PresentationLabelApproval | None,
    *,
    target_sha256: str,
) -> list[PresentationLabel]:
    """Validate an owner approval against THIS run's target and the proposed
    catalog, and project the approved view (the approved ids' entries with
    status flipped to `approved`; content is copied from the catalog, never
    from the approval). ``approval=None`` is the default and means ZERO
    approved labels. Any identity mismatch (target, catalog, or label id)
    fails closed."""
    if approval is None:
        return []
    if approval.target_sha256 != target_sha256:
        raise ValueError(
            "presentation-label approval target_sha256 does not match the current target"
        )
    if approval.catalog_sha256 != presentation_label_catalog_sha256(catalog):
        raise ValueError(
            "presentation-label approval catalog_sha256 does not match the current proposed catalog"
        )
    known = {label.label_id: label for label in catalog}
    unknown = [label_id for label_id in approval.approved_label_ids if label_id not in known]
    if unknown:
        raise ValueError(
            f"presentation-label approval references unknown label ids: {unknown}"
        )
    return [
        known[label_id].model_copy(update={"status": "approved"})
        for label_id in approval.approved_label_ids
    ]

def _heuristic_presentation_labels(catalog: list[PresentationLabel]) -> list[PresentationLabel]:
    """Recognize measured conventional headings using the product vocabulary.

    Exact aliases only: substring matches such as 'Skills Inc' must not turn
    target-person facts into reusable labels. Unknowns remain unrenderable.
    """
    from app.template_analysis.commercial.bridge import _section_source_hint

    return [
        label.model_copy(update={"status": "approved"})
        for label in catalog
        if (_section_source_hint(label.text)[0] != "additional_section"
            or " ".join(label.text.upper().split()) in {"PROJECTS", "ADDITIONAL SECTIONS"})
    ]
