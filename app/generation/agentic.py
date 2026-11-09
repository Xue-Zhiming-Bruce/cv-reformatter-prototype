"""Artifact-scoped API adapter for the existing bounded agentic runner."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from app.generation.content_validation import validate_generated_content, validate_output_structure
from app.generation import template_mapper
from app.generation.template_mapper import ClientFacingRenderContext
from app.pipeline.candidate_content import candidate_document_from_context
from app.pipeline.evidence import EvidenceRef, SelfReportedStatus, StructuralRelation, TargetStructureDraft
from app.pipeline.html_renderer import RENDERER_VERSION, render_layout_template
from app.pipeline.layout_state import C2LayoutState
from app.pipeline.runtime import RunBudget
from app.pipeline.run_models import E5LoopRecord, PresentationLabelApproval
from app.pipeline import presentation_labels, quality, review, service
from app.template_analysis.artifacts import ADOBE_RAW_FILENAME, NORMALIZED_LAYOUT_FILENAME
from app.template_analysis.commercial.models import NormalizedLayoutEvidence
from app.template_analysis.schemas import LayoutTemplateSpec


MAX_REQUEST_BYTES = 512000  # Two dense 900px page overviews, including Base64/JSON overhead.


class AgenticGenerationError(RuntimeError):
    pass


class AgenticPresentationLabelError(AgenticGenerationError):
    """The run stopped on measured presentation labels that no explicit,
    catalog-bound owner approval admits yet. ``catalog`` carries the exact
    labels and identity a reviewer must approve."""

    def __init__(self, message: str, *, catalog: dict[str, Any]) -> None:
        super().__init__(message)
        self.catalog = catalog


def _presentation_label_block(run_dir: Path) -> dict[str, Any] | None:
    """The catalog a reviewer must approve, when the privacy gate blocked on a
    proposed label (a conventional target heading the template does not
    render). Returns None for an unrelated block, so an ordinary target fact
    leak is never turned into an approval prompt."""
    listing_path = run_dir / "presentation_labels.json"
    if not listing_path.is_file():
        return None
    listing = json.loads(listing_path.read_text(encoding="utf-8"))
    proposed = [row for row in listing.get("labels") or [] if row.get("status") != "approved"]
    if not proposed:
        return None
    leaked = {
        quality._norm(line)
        for gate_file in sorted((run_dir / "lane_a").glob("hard_gates_*.json"))
        for line in (
            json.loads(gate_file.read_text(encoding="utf-8")).get("details", {})
            .get("no_target_candidate_facts", {}) or {}
        ).get("leaked_target_lines") or []
    }
    actionable = [row for row in proposed if quality._norm(row["text"]) in leaked]
    if not actionable:
        return None
    return {
        "target_sha256": listing.get("target_sha256"),
        "catalog_sha256": listing.get("catalog_sha256"),
        "labels": proposed,
        "leaked_target_lines": sorted({row["text"] for row in actionable}),
    }


def _no_reviewable_output(
    run_dir: Path, terminal: str, record: E5LoopRecord,
) -> AgenticGenerationError:
    abort = (record.summary or {}).get("abort") or {}
    if abort.get("operation") == "presentation_label_approval":
        return AgenticGenerationError(
            f"presentation-label approval was rejected: {abort.get('error')}"
        )
    catalog = _presentation_label_block(run_dir)
    if catalog is None:
        return AgenticGenerationError(
            f"Agentic run did not produce a reviewable output ({terminal}); "
            f"inspect {run_dir.name}/e5_state.json"
        )
    return AgenticPresentationLabelError(
        "Agentic run did not produce a reviewable output (" + terminal + "): the "
        "target's conventional section headings "
        + ", ".join(repr(text) for text in catalog["leaked_target_lines"])
        + " are counted as leaked target lines because no approval admits them yet. "
        "Nothing is suppressed case by case: approve this exact catalog "
        f"(catalog_sha256 {catalog['catalog_sha256']}) and retry, or upload a target "
        "whose headings the template renders.",
        catalog=catalog,
    )


def _required_gate_failure(
    version_id: str, gates: dict[str, Any], details: dict[str, Any],
) -> AgenticGenerationError:
    """Name a representation ceiling instead of charging an unrepairable gap to
    the repair agents or reporting a generic gate failure."""
    failed = [name for name, passed in gates.items() if passed is not True]
    ceiling = [
        name for name in service.E5_REPRESENTATION_CEILING_GATES if gates.get(name) is not True
    ]
    gaps = service._e5_content_shape_capability_gaps(
        details.get("content_shapes_match_evidence") or {}
    )
    if ceiling and gaps and set(failed) <= set(service.E5_REPRESENTATION_CEILING_GATES):
        return AgenticGenerationError(
            f"Representation ceiling on {version_id}: the measured properties "
            + ", ".join(gaps)
            + " are not consumed by the fixed renderer, so no agent can repair them "
            "(representation capability gap, owned by no repair layer). "
            "Reanalyze the target or upload a target whose measured properties the "
            "renderer represents."
        )
    return AgenticGenerationError(
        "Selected agentic output fails required content, privacy, structure or "
        f"rendering checks: {failed}"
    )


def generation_strategy() -> str:
    strategy = os.getenv("GENERATION_STRATEGY", "agentic").strip().lower()
    if strategy != "agentic":
        raise AgenticGenerationError("GENERATION_STRATEGY supports only agentic; standard rendering is retired")
    return strategy


def _settings() -> dict[str, int]:
    result = {}
    for name, default, low, high in (
        ("max_model_requests", 12, 2, 60),
        ("max_tool_calls", 24, 2, 100),
        ("max_repair_rounds", 2, 0, 3),
    ):
        try:
            value = int(os.getenv(f"AGENTIC_{name.upper()}", str(default)))
        except ValueError as exc:
            raise AgenticGenerationError(f"AGENTIC_{name.upper()} must be an integer") from exc
        if not low <= value <= high:
            raise AgenticGenerationError(f"AGENTIC_{name.upper()} must be between {low} and {high}")
        result[name] = value
    return result


def agentic_renderer_version() -> str:
    # Approval covers the renderer, prompts, models and execution envelope.
    contract = {
        "image_encoding": "lossless-grayscale-png/1",
        "review_image_encoding": review.REVIEW_IMAGE_ENCODING_VERSION,
        "builder_image_encoding": review.REVIEW_IMAGE_ENCODING_VERSION,
        "measurement_policy": service.HEADER_VERTICAL_RULE_MEASUREMENT_VERSION,
        "header_contact_label_policy": service.HEADER_CONTACT_LABEL_MEASUREMENT_VERSION,
        "review_measurement_contract": review.REVIEW_MEASUREMENT_CONTRACT_VERSION,
        "review_presentation_facts": review.REVIEW_PRESENTATION_FACTS_VERSION,
        "pdf_structure_checks": quality.PDF_STRUCTURE_VALIDATION_VERSION,
        "candidate_fact_privacy": quality.PRIVACY_GATE_VERSION,
        "client_disclosure": template_mapper.CLIENT_DISCLOSURE_VERSION,
        "renderer": RENDERER_VERSION, "settings": {**_settings(), "max_request_bytes": MAX_REQUEST_BYTES},
        "models": service._model_identity(),
        "prompts": [service.E5_LANE_A_BUILDER_INSTRUCTIONS, service.E5_REVIEWER_INSTRUCTIONS,
                    service.E5_ATTRIBUTION_INSTRUCTIONS],
        "provider_routes": [hashlib.sha256(value.encode()).hexdigest() for value in (
            os.getenv("DEEPSEEK_BASE_URL") or "https://api.deepseek.com",
            os.getenv("VISUAL_API_BASE") or "",
        )],
    }
    digest = hashlib.sha256(json.dumps(contract, sort_keys=True).encode()).hexdigest()[:16]
    return f"agentic-a/6+{RENDERER_VERSION}+{digest}"


def _structure_basis(state: C2LayoutState, normalized: NormalizedLayoutEvidence) -> TargetStructureDraft:
    """Project only uniquely measured headings; never invent a relation draft."""
    pages = {page.page_number: page for page in normalized.pages}
    claims = []
    missing = []
    for heading in (node for node in state.nodes if node.kind == "heading"):
        matches = [block for block in normalized.text_blocks
                   if block.text.strip().casefold() == heading.label.casefold() and block.bbox is not None]
        if len(matches) != 1:
            missing.append(heading.parent_id)
            continue
        block = matches[0]
        page, box = pages.get(block.page_number), block.bbox
        if page is None:
            raise AgenticGenerationError("Normalized heading evidence references a missing target page")
        claims.append(StructuralRelation(
            claim_id=f"template.{heading.node_id}", relation="section_boundary", child=heading.parent_id,
            statement="Product-template heading matched to normalized target measurement",
            confidence=1, status="confirmed_by_shell", evidence=[EvidenceRef(
                evidence_id=f"template.{block.element_id}", kind="adobe_element",
                page_number=block.page_number, source_kind="adobe_json", element_id=block.element_id,
                bbox_pt=[box.x0*page.width_pt, box.top*page.height_pt, box.x1*page.width_pt, box.bottom*page.height_pt],
            )],
        ))
    return TargetStructureDraft(
        target_id="uploaded_product_template", investigator="scripted", structure=claims,
        self_reported=SelfReportedStatus(status="partial" if missing else "ok",
            sections_expected=len(claims)+len(missing), sections_identified=len(claims),
            notes=f"LayoutTemplateSpec is authoritative; headings without unique measurements: {missing}"),
    )


def _proof_label_approval(
    layout_proof_approval: Any, catalog: dict[str, Any],
    *, target_checksum: str, layout_spec_checksum: str,
) -> Any:
    """Reuse the owner's layout-proof approval as this run's label approval.

    The proof approval records that the owner approved exactly these labels for
    this target, so it carries over ONLY when it is approved, names this exact
    target and layout spec, and its catalog identity equals the catalog this run
    just derived. Anything else (no approval, a stale catalog, another target)
    still needs a new explicit approval. Artifact scope is enforced by
    ``render_candidate_document`` before the run starts."""
    if (
        layout_proof_approval is None
        or not layout_proof_approval.is_approved
        or not layout_proof_approval.presentation_labels
        or layout_proof_approval.target_checksum != target_checksum
        or layout_proof_approval.layout_spec_checksum != layout_spec_checksum
        or layout_proof_approval.presentation_label_catalog_sha256 != catalog["catalog_sha256"]
    ):
        return None
    return PresentationLabelApproval(
        target_sha256=target_checksum,
        catalog_sha256=catalog["catalog_sha256"],
        approved_label_ids=sorted({
            label_id
            for entry in layout_proof_approval.presentation_labels
            for label_id in entry.approved_label_ids
        }),
    )


def render_agentic(
    *, context: ClientFacingRenderContext, layout_spec: LayoutTemplateSpec,
    target_pdf: Path, evidence_dir: Path, output_dir: Path,
    presentation_label_approval: Any = None,
    layout_proof_approval: Any = None,
) -> tuple[Path, Path, dict[str, Any]]:
    """Run A's real live loop; return a checked selected version, never the last attempt."""
    identity = agentic_renderer_version()
    settings = _settings()
    if not os.getenv("DEEPSEEK_API_KEY") and not (os.getenv("DEEPSEEK_BASE_URL") and os.getenv("OPENAI_API_KEY")):
        raise AgenticGenerationError("Configure DEEPSEEK_API_KEY, or an explicit compatible DEEPSEEK_BASE_URL and OPENAI_API_KEY")
    if not output_dir.resolve().is_relative_to(evidence_dir.resolve()):
        raise AgenticGenerationError("Agentic run artifacts must stay inside their owning artifact directory")
    if target_pdf.suffix.lower() != ".pdf" or not target_pdf.is_file():
        raise AgenticGenerationError("Agentic generation requires an uploaded, measured target PDF")
    raw_path, normalized_path = evidence_dir/ADOBE_RAW_FILENAME, evidence_dir/NORMALIZED_LAYOUT_FILENAME
    if not raw_path.is_file() or not normalized_path.is_file():
        raise AgenticGenerationError("Agentic generation requires the target's saved Adobe and normalized evidence")
    normalized = NormalizedLayoutEvidence.model_validate_json(normalized_path.read_text())
    import pdfplumber

    candidate = candidate_document_from_context(context, candidate_id="reviewed-api-candidate",
        include_subheading=layout_spec.header.show_candidate_subheading)
    with pdfplumber.open(target_pdf) as pdf:
        state, _, _ = render_layout_template(layout_spec, candidate,
            target_sha256=service._sha256_file(target_pdf), page_count=len(pdf.pages))
    if presentation_label_approval is None:
        catalog = presentation_labels.template_presentation_label_catalog(
            state, normalized, target_sha256=service._sha256_file(target_pdf))
        from app.template_analysis.layout_proof_artifacts import layout_spec_checksum

        presentation_label_approval = _proof_label_approval(
            layout_proof_approval, catalog,
            target_checksum=service._sha256_file(target_pdf),
            layout_spec_checksum=layout_spec_checksum(layout_spec),
        )
        if presentation_label_approval is None and catalog["unrecognized_template_ids"]:
            raise AgenticPresentationLabelError(
                "unrecognized product-template presentation labels require explicit "
                f"identity-bound label approval: {catalog['unrecognized_template_ids']}. "
                "Nothing is suppressed case by case: approve this exact catalog "
                f"(catalog_sha256 {catalog['catalog_sha256']}) and retry, or upload a target "
                "whose headings the template renders.",
                catalog={
                    "target_sha256": catalog["target_sha256"],
                    "catalog_sha256": catalog["catalog_sha256"],
                    "labels": [row.model_dump(mode="json") for row in catalog["labels"]],
                    "leaked_target_lines": [
                        row.text for row in catalog["labels"]
                        if row.label_id in catalog["unrecognized_template_ids"]
                    ],
                },
            )
    draft = _structure_basis(state, normalized)
    output_dir.mkdir(parents=True, exist_ok=False)
    draft_path = output_dir/"structure_draft.json"
    draft_path.write_text(draft.model_dump_json(indent=2))
    run_dir, terminal, raw_record = service.run_e5(
        target_pdf, output_dir/"pipeline", candidate=candidate, adobe_json=raw_path,
        normalized_evidence=normalized_path, structure_draft=draft_path,
        layout_spec=layout_spec, live=True, lanes=("a",),
        max_repair_rounds=settings["max_repair_rounds"], builder_reserve_requests=4,
        presentation_label_approval=presentation_label_approval,
        budget=RunBudget(max_model_requests=settings["max_model_requests"],
            max_tool_calls=settings["max_tool_calls"], max_request_bytes=MAX_REQUEST_BYTES),
    )
    record = E5LoopRecord.model_validate(raw_record)
    lane = record.lanes.get("a")
    if terminal != "ready_for_owner_review" or lane is None:
        raise _no_reviewable_output(run_dir, terminal, record)
    version, stem, selection = service._selected_lane_artifact(lane)
    if version is None or selection != "BEST" or not version.hard_gates_passed:
        raise AgenticGenerationError("Agentic run has no validated selected output")
    if version.pdf_sha256 not in lane.summary.get("reviewed_pdf_sha256", []):
        raise AgenticGenerationError("Selected agentic output has not completed visual Reviewer review")
    if not record.summary.get("source_identity_stable"):
        raise AgenticGenerationError("Pipeline source changed during agentic generation; run again")
    html_path, pdf_path = run_dir/f"lane_a/{stem}.html", run_dir/f"lane_a/{stem}.pdf"
    if service._sha256_file(html_path) != version.html_sha256 or service._sha256_file(pdf_path) != version.pdf_sha256:
        raise AgenticGenerationError("Selected agentic output changed after validation")
    gates = json.loads((run_dir/f"lane_a/hard_gates_{version.version_id}.json").read_text())
    gate_validation = service._e5_hard_gate_record(version.version_id, gates["gates"], gates.get("details", {}),
        blocking_gates=service.LANE_A_OUTPUT_GATES)
    gate_validation.pop("details")  # Detailed evidence stays in the owning run directory.
    if not gate_validation["passed"]:
        raise _required_gate_failure(version.version_id, gates["gates"], gates.get("details", {}))
    content = validate_generated_content(context, pdf_path=pdf_path, layout_spec=layout_spec)
    structure = validate_output_structure(context, html_path=html_path, pdf_path=pdf_path,
        layout_spec=layout_spec)
    if not content.passed or not structure.passed:
        raise AgenticGenerationError("Selected agentic output fails product content or structure validation")
    if agentic_renderer_version() != identity:
        raise AgenticGenerationError("Agentic model, prompt or execution configuration changed during generation")
    report = {"renderer_version": identity, "generation_strategy": "agentic",
        "terminal_state": terminal, "selected_version": version.version_id,
        "open_findings": lane.open_findings, "gate_validation": gate_validation, "budget": record.budget_state,
        "summary": lane.summary, "run_directory": run_dir.resolve().relative_to(evidence_dir.resolve()).as_posix(),
        "content_validation": content.model_dump(mode="json"),
        "structure_validation": structure.model_dump(mode="json"), "client_ready": False}
    (output_dir/"agentic_generation.json").write_text(json.dumps(report, indent=2)+"\n")
    return html_path, pdf_path, report
