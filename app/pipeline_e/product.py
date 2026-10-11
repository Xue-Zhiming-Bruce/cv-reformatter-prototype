"""Local product admission and deterministic reuse of E's selected renderer."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from typing import Literal

import pdfplumber
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.extraction.candidate_schema import CandidateProfile
from app.generation.content_validation import validate_generated_content
from app.generation.template_mapper import build_client_render_context
from app.pipeline_e.candidate_boundary import _apply_header_overflow_disposition
from app.pipeline_e.resume_layout.candidates import candidate_from_profile
from app.pipeline_e.resume_layout.fonts import font_file_for_family, pdf_font_role_gate
from app.pipeline_e.resume_layout.gates import blank_page_gate, orphan_tail_page_gate, readable_body_gate
from app.pipeline_e.resume_layout.render import _export_pinned_html_to_pdf
from app.pipeline_e.resume_layout.state import C2LayoutState, validate_layout_state
from app.pipeline_e.shared import PresentationLabel, _sha256_file
from app.pipeline_e.template import (
    AuthoredSlot, AuthoredTemplateCandidate, authored_network_disabled_environment,
    authored_pdf_presence_gate, authored_privacy_gate, build_authored_document,
    fill_authored_template, presentation_label_markers, validate_authored_template,
)
from app.pipeline_e.template_build import build_template


class LayoutTemplateSpec(C2LayoutState):
    """Measured flow vocabulary promoted without HTML/CSS (ADR 0007)."""
    schema_version: Literal["3.0"] = "3.0"
    structure_contract: Literal["measured"] = "measured"


class FontRole(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    target_family: str
    required_family: str
    font_size_pt: float = Field(gt=0)
    bold: bool
    font_file: str | None = None
    bold_face_file: str | None = None
    substitution: bool
    visual_review_required: bool


class RendererArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["e-renderer-artifact/1"] = "e-renderer-artifact/1"
    renderer_version: Literal["pipeline-e-html/1"] = "pipeline-e-html/1"
    layout_spec_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    font_manifest: dict[str, str]
    template: AuthoredTemplateCandidate
    labels: list[PresentationLabel]
    font_roles: dict[Literal["body", "name", "section_heading", "entry_heading", "name_secondary"], FontRole]

    @model_validator(mode="after")
    def required_roles(self) -> "RendererArtifact":
        if not {"body", "name"} <= set(self.font_roles):
            raise ValueError("Renderer artifact requires measured body and name typography")
        return self


def spec_checksum(spec: LayoutTemplateSpec) -> str:
    return hashlib.sha256(json.dumps(spec.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def font_manifest(roles: dict[str, FontRole]) -> dict[str, str]:
    directory = Path(__file__).parent / "assets/fonts"
    manifest = {p.name: _sha256_file(p) for p in sorted(directory.glob("*.woff2"))}
    for role in roles.values():
        bundled = directory / (role.required_family.casefold().replace(" ", "") + "-regular.woff2")
        if bundled.exists():
            continue
        font = font_file_for_family(role.required_family)
        if font is None:
            raise ValueError("Required font is unavailable: " + role.required_family)
        manifest["installed:" + role.required_family] = _sha256_file(font)
    return manifest


def admit_run(run_dir: Path, target: Path, destination: Path) -> None:
    """Bind the selected, evidence-qualified template; never adopt the last attempt."""
    record = json.loads((run_dir / "e5_state.json").read_text())
    if not record["summary"].get("source_identity_stable"):
        raise ValueError("Pipeline source changed during construction; rebuild the template")
    lane = record["lanes"]["b"]
    selected = lane["best_render_version"]
    attempts = [item for item in lane["builder_candidates"] if item["candidate_render_version"] == selected and item["validation"].get("passed")]
    if not selected or len(attempts) != 1:
        raise ValueError("No unique safety-valid selected template is available")
    template = AuthoredTemplateCandidate.model_validate(attempts[0]["typed_candidate"])
    config = json.loads((run_dir / "run_config.json").read_text())
    preflight = json.loads((run_dir / "target_preflight.json").read_text())
    approved = set(config["presentation_label_approval"]["renderable_label_ids"])
    labels = [PresentationLabel.model_validate(item) for item in preflight["proposed_presentation_labels"] if item["label_id"] in approved]
    # Status is a shell-issued admission decision, not the Builder's claim.
    manual = set(config["presentation_label_approval"]["approved_label_ids"])
    labels = [label.model_copy(update={"status": "approved" if label.label_id in manual else "evidence_qualified"}) for label in labels]
    state = C2LayoutState.model_validate_json((run_dir / "c2_layout_state.json").read_text())
    safe_text = {label.text for label in labels}
    payload = state.model_dump(mode="json", exclude={"schema_version", "warnings"})
    for node in payload["nodes"]:
        if node.get("label") and node["label"] not in safe_text:
            node["label"] = None
    # Detailed analysis explanations stay in evidence; the neutral contract keeps gaps explicit.
    for gap in payload["capability_gaps"]:
        gap["reason"] = "Unsupported presentation feature; inspect target evidence before reuse."
    # Old C2 margins describe a body scaffold. V3 page margins describe the
    # visible full-page frame, including measured sidebar labels and rules.
    with pdfplumber.open(target) as document:
        first = document.pages[0]
        boxes = [char for char in first.chars if str(char.get("text", "")).strip()]
        boxes += [obj for obj in first.lines + first.rects + first.curves
                  if (obj["width"] >= 1 or obj["height"] >= 10)
                  and not (obj["x0"] <= .5 and obj["x1"] >= first.width - .5 and obj["top"] <= .5 and obj["bottom"] >= first.height - .5)]
        if not boxes:
            raise ValueError("Target has no measurable first-page content frame")
        page = payload["page"]
        page.update({
            "margin_left_pt": max(0, min(float(box["x0"]) for box in boxes)),
            "margin_right_pt": max(0, page["width_pt"] - max(float(box["x1"]) for box in boxes)),
            "margin_top_pt": max(0, min(float(box["top"]) for box in boxes)),
            "margin_bottom_pt": max(0, page["height_pt"] - max(float(box["bottom"]) for box in boxes)),
            "evidence_ids": ["local_pdf.first_page_content_frame"],
        })
        if len(document.pages) > 1:
            payload["capability_gaps"].append({"feature": "per_page_frame_variants", "reason": "The local prototype reuses the measured first-page frame; continuation-page variants require visual review.", "evidence_ids": ["local_pdf.first_page_content_frame"]})
    spec = LayoutTemplateSpec.model_validate(payload)
    if validate_layout_state(spec) or spec.provenance.target_sha256 != _sha256_file(target):
        raise ValueError("Measured layout contract does not validate against the target")
    roles = {key: FontRole.model_validate(value) for key, value in json.loads((run_dir / "target_font_roles.json").read_text()).items()}
    artifact = RendererArtifact(
        layout_spec_sha256=spec_checksum(spec), target_sha256=_sha256_file(target),
        font_manifest=font_manifest(roles), template=template, labels=labels,
        font_roles=roles,
    )
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "layout_template_spec.json").write_text(spec.model_dump_json(indent=2))
    (destination / "renderer_artifact.json").write_text(artifact.model_dump_json(indent=2))


class LeafParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.counts: Counter[str] = Counter()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        value = dict(attrs).get("data-candidate-leaf")
        if value:
            self.counts[value] += 1


def material_geometry(pdf: Path, labels: set[str]) -> dict[str, object]:
    outside: list[dict[str, object]] = []
    overlaps: list[dict[str, object]] = []
    orphans: list[dict[str, object]] = []
    with pdfplumber.open(pdf) as document:
        for index, page in enumerate(document.pages, 1):
            words = page.extract_words()
            for word in words:
                if word["x0"] < -0.5 or word["top"] < -0.5 or word["x1"] > page.width + 0.5 or word["bottom"] > page.height + 0.5:
                    outside.append({"page": index, "text": word["text"]})
            for i, left in enumerate(words):
                for right in words[i + 1:]:
                    dx = min(left["x1"], right["x1"]) - max(left["x0"], right["x0"])
                    dy = min(left["bottom"], right["bottom"]) - max(left["top"], right["top"])
                    area = min((left["x1"]-left["x0"])*(left["bottom"]-left["top"]), (right["x1"]-right["x0"])*(right["bottom"]-right["top"]))
                    if dx > 0 and dy > 0 and area > 0 and dx * dy / area > 0.5:
                        overlaps.append({"page": index, "left": left["text"], "right": right["text"]})
            lines = (page.extract_text() or "").strip().splitlines()
            if lines and lines[-1].strip() in labels:
                orphans.append({"page": index, "heading": lines[-1].strip()})
    return {"passed": not outside and not overlaps and not orphans, "outside_page": outside, "overlap": overlaps, "orphan_headings": orphans}


def render_preview(directory: Path, output: Path, target: Path, profile: CandidateProfile, *, blind: bool) -> dict[str, object]:
    spec = LayoutTemplateSpec.model_validate_json((directory / "layout_template_spec.json").read_text())
    artifact = RendererArtifact.model_validate_json((directory / "renderer_artifact.json").read_text())
    if artifact.layout_spec_sha256 != spec_checksum(spec) or artifact.target_sha256 != _sha256_file(target) or spec.provenance.target_sha256 != artifact.target_sha256 or artifact.font_manifest != font_manifest(artifact.font_roles):
        raise ValueError("Target, template or font identity changed; rebuild the template")
    candidate = _apply_header_overflow_disposition(candidate_from_profile(profile, blind_profile=blind), spec)
    template = artifact.template
    name_binding = "verified_split_name"
    if "{{candidate:given_name}}" in template.html and (not candidate.profile or not candidate.profile.given_name or not candidate.profile.family_name):
        # Use the existing full-name value; never infer missing name parts.
        template = template.model_copy(update={
            "html": template.html.replace("{{candidate:given_name}}", "{{candidate:name}}").replace("{{candidate:family_name}}", ""),
            "slots": [slot for slot in template.slots if slot.category not in {"candidate_given_name", "candidate_family_name"}] + [AuthoredSlot(category="candidate_name", required=True)],
        })
        name_binding = "full_name"
    # Product content cannot use CSS concealment or spoof shell-owned identities.
    # Empty optional boxes may disappear; populated candidate boxes may not.
    css = template.css
    for match in list(re.finditer(r"([^{}]+)\{([^{}]*)\}", css)):
        selectors = match.group(1).strip().split(",")
        if all(re.fullmatch(r"[.#a-zA-Z0-9_\- :>]+:empty", selector.strip()) for selector in selectors):
            css = css.replace(match.group(0), "")
    if re.search(r'(?:overflow(?:-[xy])?\s*:\s*(?:hidden|clip)|visibility\s*:\s*hidden|display\s*:\s*none|opacity\s*:\s*0(?:\D|$)|(?:clip|clip-path|text-overflow)\s*:)', css, re.I) or re.search(r'data-(?:candidate-leaf|field)\s*=', template.html, re.I):
        raise ValueError("Template uses concealment or reserved candidate identities")
    if "\\" in css or re.search(r"(?:opacity|filter|clip|mask|z-index)\s*:|color\s*:\s*transparent", css, re.I):
        raise ValueError("Template uses unsupported concealed/layered text styling")
    validate_authored_template(template, target_pdf=target, render_candidate=candidate, labels=artifact.labels, known_evidence_ids=set())
    fill = fill_authored_template(template, candidate, artifact.labels)
    parser = LeafParser(); parser.feed(fill.html_filled)
    leaf_ids = {leaf.leaf_id for leaf in candidate.leaves if leaf.text}
    bad_leaves = sorted({key for key in leaf_ids if parser.counts[key] != 1} | (set(parser.counts) - leaf_ids))
    if fill.missing_leaves or bad_leaves:
        raise ValueError("Candidate values must be rendered exactly once: " + ", ".join(sorted(set(fill.missing_leaves + bad_leaves))))
    doc = build_authored_document(template, fill, (spec.page.width_pt, spec.page.height_pt), page_margins_pt=(spec.page.margin_top_pt, spec.page.margin_right_pt, spec.page.margin_bottom_pt, spec.page.margin_left_pt))
    output.mkdir(parents=True, exist_ok=True)
    html = output / "candidate_profile.html"; pdf = output / "candidate_profile.pdf"
    html.write_text(doc)
    environment = authored_network_disabled_environment()
    _export_pinned_html_to_pdf(html, pdf, environment)
    context = build_client_render_context(profile, blind_profile=blind)
    referenced = set(presentation_label_markers(template.html))
    labels = {label.text for label in artifact.labels if label.label_id in referenced}
    content = validate_generated_content(context, pdf_path=pdf, contact_values_only=True)
    gates = {
        "content": content.model_dump(mode="json"),
        "pdf_presence": authored_pdf_presence_gate(fill, candidate, pdf),
        "privacy": authored_privacy_gate(doc, pdf, target, labels=labels, source_texts={leaf.text for leaf in candidate.leaves if leaf.text}),
        "blank_pages": blank_page_gate(pdf),
        "tail_page": orphan_tail_page_gate(pdf),
        "readable_body": readable_body_gate(pdf, minimum_pt=min(10.0, artifact.font_roles["body"].font_size_pt)),
        "geometry": material_geometry(pdf, labels),
        "font_roles": pdf_font_role_gate(pdf, {key: value.model_dump() for key, value in artifact.font_roles.items()}, candidate, [label for label in artifact.labels if label.label_id in referenced]),
    }
    (output / "quality_gates.json").write_text(json.dumps(gates, indent=2))
    if not all(gate["passed"] for gate in gates.values()):
        failed = [key for key, gate in gates.items() if not gate["passed"]]
        raise ValueError("PDF failed required checks: " + ", ".join(failed))
    return {"layout_template_version": spec.template_version, "layout_spec_sha256": spec_checksum(spec), "renderer_version": artifact.renderer_version, "chrome_version": environment["version"], "font_manifest": artifact.font_manifest, "quality_gates_passed": True, "renderer_artifact_sha256": _sha256_file(directory / "renderer_artifact.json"), "header_name_binding": name_binding}


def construct_template(target: Path, directory: Path, profile: CandidateProfile, *, blind: bool, live: bool) -> None:
    run, terminal, _ = build_template(target, directory / ("construction_" + uuid.uuid4().hex), candidate_profile=profile, blind_profile=blind, live=live, max_repair_rounds=3)
    if terminal != "ready_for_owner_review":
        raise ValueError(f"Template construction stopped: {terminal}; inspect the local construction evidence")
    admit_run(run, target, directory)
