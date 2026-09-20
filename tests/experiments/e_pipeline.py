"""Pipeline E0/E1 — evidence-grounded target-understanding experiment.

Owner-authorized experiment under `tests/experiments/` (PIPELINE_E_PLAN.md §4/§7,
D_PIPELINE_PROPOSAL.md §14). Not part of the active product architecture and not
an approved roadmap item.

E0 freezes the reviewed input set and the E1 rubric. E1 exposes raw target
evidence to one bounded Target Investigator over the existing Pipeline D
runtime:

- page overviews (downscaled, for page count/columns/sectioning only);
- on-demand ORIGINAL-RESOLUTION region crops of the target or of a render,
  always recording the source, the scale transform, and the page size so the
  crop keeps its location in the whole page;
- read-only lookups into the untouched Adobe `structuredData.json` tree,
  returning verbatim node slices with path/page/bbox/Bounds and the raw
  (pre-normalization) text-leaf count;
- permitted pdfplumber measurements (characters, words, long horizontal rules)
  from the same PDF bytes, carrying `local_pdf` provenance;
- a coverage audit that reports raw-to-normalized loss and missing raw leaves
  as separate classes.

Two boundaries this module enforces by construction:

1. The investigator never receives normalized evidence as a substitute for
   original evidence. `EvidencePod` reads only `structured_data` from the raw
   Adobe JSON; `normalize_adobe_layout` is imported lazily and only for the
   coverage audit, where the normalized side is the *object of measurement*,
   never the evidence handed to the agent.
2. A model may hypothesize structure but never edit evidence. Every tool is
   read-only, budget-counted, and traced by the Pipeline D `RunBudget` /
   `RunTrace`. The shell assigns evidence ids; the model cites them.

Reuse (no parallel runner, no second renderer, no second evidence store): the
`RunBudget` counter and `RunTrace` from `tests.experiments.d_pipeline`, D's
run-directory/`manifest.json` versioning conventions, its deterministic
`owner_decide` promotion CLI, its `_limits`/`_record_usage` usage plumbing, and
its injected-`base_html` `EvidenceStore`; page rendering comes from
`tests.experiments.a_pipeline._render_pages` (pypdfium2, >=2x scale). E1 adds
only the evidence-access layer D lacks — `EvidenceStore` is constructed here
directly rather than by calling `run_d0`, because D's shell begins with the
heading-rule repair state machine, which is not what E1 exercises.

Offline by default. No model or provider call happens unless the caller passes
`--live` and the repository's authorization/policy gates are satisfied; the
scripted path (`ScriptedTargetInvestigator`) is the mandatory verification path
and still performs real image rendering and real pdfplumber measurement.

Usage:

    python -m tests.experiments.e_pipeline --target PDF --adobe-json JSON \
        [--out DIR] [--live]

--- E2 (PIPELINE_E_PLAN.md §8, revision 2026-09-20) ---

`run_e2` is the smallest coherent See -> Measure -> Attribute -> Repair ->
Re-render loop through the EXISTING components (no second runner, renderer,
corpus, or artifact layout; prep note §4):

- Target presentation compiles through the C2 chain already used by the
  canonical experiment (`_analyze_target` persistent cache ->
  `compile_layout_state` -> `validate_layout_state`); candidate content is the
  frozen, author-segmented `candidate_resume_E` render context (the same
  content the canonical C2-0b pair E->F renders). DOCX stays out (plan §5.6).
- Rendering reuses C2's `compile_render_plan`/`render_html` and the pinned
  Chrome exporter; delivery gates reuse the C2 hard-gate functions verbatim
  (content/privacy vs the FROZEN C1 baseline target/structure/blank/accounting
  /content-shape verification + double-render line stability).
- The Visual Reviewer (`ScriptedReviewer` offline, PydanticAI `--live`)
  produces observation-first `DefectFinding`s bound to exact versions; it
  never decides root cause and never approves delivery.
- `MeasureController` executes typed `MeasurementRequest`s with pdfplumber on
  the ACTUAL final PDF bytes (role-aligned page-local word/rule geometry;
  deduplicated rules; page numbers always carried), classifying
  confirmed/falsified/ambiguous/evidence_missing/not_measurable. VLM
  confidence never overrides a measurement status.
- `AttributionRecord` keeps observation and causal hypothesis separate (a
  falsified hypothesis never deletes the finding).
- The Builder/Repair agent proposes a typed `RepairProposal` against a bounded
  layer vocabulary; the deterministic shell validates authorization, base
  version, and layer scope, renders a CANDIDATE render version, repeats the
  IDENTICAL measurement request, and promotes only on verified improvement
  with all gates green. A failed repair rolls back; the best valid render is
  always preserved.
- The Reviewer may never approve its own repair: the shell refuses a finding
  whose reviewer == the repair agent of the newest render version.
- Loop exits: `delivered_pending_owner` (all gates green + no unreviewed
  material regions) or `budget_exhausted` (budget end, never success — the
  best valid version, open findings, attempted strategies, and resumable
  state are persisted). `operational_abort` describes the failed operation
  and never classifies the template as unsupported.

E2 offline terminal is always `awaiting_owner_review`: the owner performs the
final visual acceptance of the T-v1 render; automated metrics declare nothing
(plan §13). The content-shape probe profiles reuse the C2 independent
fixtures (short/medium/long) through `run_flow_probe` as a bounded
proxy-shape rehearsal while the canonical probe lane stays with C2.

Usage (E2):

    python -m tests.experiments.e_pipeline --e2 --target PDF \
        [--out DIR] [--live]

Offline tests: `tests/experiments/test_e_pipeline.py`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, model_validator

from tests.experiments.a_pipeline import (
    RUNS,
    _analyze_target,
    _export_pinned_html_to_pdf,
    _render_pages,
)
from tests.experiments.d_pipeline import (
    CheckpointBudgetExceeded,
    EvidenceStore,
    RunBudget,
    RunTrace,
    _limits,
    _record_usage,
    owner_decide,
)
from tests.experiments.c2_docx_build import TOLERANCE_PT
from tests.experiments.c_pipeline import (
    derive_body_scaffold,
    derive_body_tier_targets,
    derive_header_scaffold,
    pinned_export_environment,
)

POD_SCHEMA_VERSION = "pipeline-e-evidence-pod/1"
DRAFT_SCHEMA_VERSION = "pipeline-e-structure-draft/1"
OVERVIEW_MAX_EDGE = 900  # long-edge pixels for a page overview
MAX_CROP_SIDE_PT = 400.0


# --- typed contracts ---------------------------------------------------------


class EvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


Admissibility = Literal["admissible", "diagnostic_only"]
EvidenceKind = Literal[
    "page_overview",
    "region_crop",
    "adobe_element",
    "local_measurement",
    "coverage_audit",
]
EvidenceStatus = Literal["available", "unavailable", "unsupported"]


class EvidenceRef(EvidenceModel):
    """Proof that a claim came from a specific place in the original evidence.

    `admissibility` is the shell's, not the model's: target person facts may be
    cited as `diagnostic_only` for layout inference, and can never become
    candidate fill values (PIPELINE_E_PLAN.md §3.1).
    """

    evidence_id: str
    kind: EvidenceKind
    page_number: int | None = Field(default=None, ge=1)
    bbox_pt: list[float] | None = None  # [x0, top, x1, bottom] in PDF points
    source_kind: Literal["target_pdf", "adobe_json", "render"] | None = None
    source_path: str | None = None  # path inside the raw Adobe tree, or run file name
    element_id: str | None = None  # Adobe ObjectID / normalized element id
    artifact: str | None = None  # written image/measurement file backing this ref
    admissibility: Admissibility = "admissible"

    @model_validator(mode="after")
    def bbox_has_four_numbers(self) -> "EvidenceRef":
        if self.bbox_pt is not None and len(self.bbox_pt) != 4:
            raise ValueError("bbox_pt must be [x0, top, x1, bottom]")
        return self


class EvidenceAccessRecord(EvidenceModel):
    """One evidence access attempt and its outcome.

    An unavailable or unsupported source is a recorded outcome, never a silent
    skip: `reason` is mandatory in both cases (PIPELINE_E_PLAN.md §8).
    """

    evidence_id: str
    kind: EvidenceKind
    provider: str
    provider_version: str | None = None
    tool: str | None = None
    status: EvidenceStatus
    reason: str | None = None
    source_path: str | None = None
    artifact: str | None = None
    request: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def unavailable_states_are_explained(self) -> "EvidenceAccessRecord":
        if self.status in ("unavailable", "unsupported") and not self.reason:
            raise ValueError(f"status={self.status} requires a reason")
        return self


class StructuralRelation(EvidenceModel):
    """One evidence-linked structural claim (hierarchy, order, or inline role).

    `relation` stays a free string: E1 must be able to report a relation it
    cannot classify yet, rather than being forced into a vocabulary that hides
    the unfamiliar case. The shell records it; it never becomes product state.
    """

    claim_id: str
    relation: Literal[
        "reading_order",
        "section_boundary",
        "ownership",
        "inline_role",
        "repetition",
        "no_extraction_counterpart",
    ]
    parent: str | None = None
    child: str | None = None
    relation_kind: str | None = None
    statement: str
    evidence: list[EvidenceRef] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    status: Literal["proposed", "confirmed_by_shell", "rejected_by_shell"] = "proposed"


class UnresolvedItem(EvidenceModel):
    """An honest stop: the investigator could not decide this from evidence.

    There is deliberately NO mapping from this item to an owner decision: an
    unresolved item requires further evidence, not owner adjudication.
    """

    item_id: str
    question: str
    status: Literal["unresolved", "unsupported"]
    reason: str
    evidence: list[EvidenceRef] = Field(default_factory=list)
    evidence_gap: Literal[
        "normalization_loss",
        "missing_from_raw_extraction",
        "ambiguous_relation",
        "no_tool",
        "unauthorized_source",
    ]


class SelfReportedStatus(EvidenceModel):
    """The model's own claim about its understanding — never an acceptance.

    Confidence wrongness is a failure (PIPELINE_E_PLAN.md §8, E1 stop gate), so
    `ok` here is recorded and then checked by the deterministic shell.
    """

    status: Literal["ok", "partial", "insufficient_evidence", "unsupported"]
    sections_expected: int | None = Field(default=None, ge=0)
    sections_identified: int | None = Field(default=None, ge=0)
    notes: str = ""


class TargetStructureDraft(EvidenceModel):
    """The E1 judgment artifact: claims + evidence pointers + unresolved items."""

    schema_version: Literal["pipeline-e-structure-draft/1"] = DRAFT_SCHEMA_VERSION
    target_id: str
    investigator: Literal["llm", "scripted"]
    structure: list[StructuralRelation] = Field(default_factory=list)
    unresolved: list[UnresolvedItem] = Field(default_factory=list)
    self_reported: SelfReportedStatus
    evidence_used_by_id: dict[str, EvidenceAccessRecord] = Field(default_factory=dict)


# --- evidence pod ------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _raw_page_sizes(structured_data: dict[str, Any]) -> dict[int, tuple[float, float]]:
    sizes: dict[int, tuple[float, float]] = {}
    for page in structured_data.get("pages") or []:
        number = int(page.get("page_number", len(sizes))) + 1  # Adobe pages are 0-based
        sizes[number] = (float(page.get("width") or 0.0), float(page.get("height") or 0.0))
    return sizes


def _iter_raw_nodes(node: Any, path: str = ""):
    """Yield every dict node of the raw Adobe tree with its structural path."""
    if isinstance(node, dict):
        yield path, node
        for key, value in node.items():
            yield from _iter_raw_nodes(value, f"{path}/{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _iter_raw_nodes(value, f"{path}[{index}]")


def _raw_text_leaves(structured_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Every leaf that carries non-empty `Text` in the ORIGINAL provider response.

    This is deliberately the pre-normalization count. Resume I had 122 such
    leaves against 58 normalized text blocks (C2_RESUME_I_BLIND_STRUCTURE_AUDIT.md);
    the two numbers must stay separately reported.
    """
    leaves: list[dict[str, Any]] = []
    for _path, node in _iter_raw_nodes(structured_data.get("elements") or []):
        text = node.get("Text")
        if isinstance(text, str) and text.strip():
            leaves.append(
                {
                    "path": str(node.get("Path") or ""),
                    "page_number": int(node.get("Page", 0)) + 1,
                    "text": text,
                    "object_id": node.get("ObjectID"),
                    "bounds": node.get("Bounds"),
                }
            )
    return leaves


def audit_evidence_coverage(
    structured_data: dict[str, Any], normalized_texts: list[str]
) -> dict[str, Any]:
    """Report raw-to-normalized loss and Text-less leaves as SEPARATE classes.

    The normalized side is passed in by the caller: this function measures the
    production normalization, it never stands in for original evidence.
    """
    leaves = _raw_text_leaves(structured_data)
    squeezed = {_squeeze(text) for text in normalized_texts}
    matched, dropped = [], []
    for leaf in leaves:
        (matched if _squeeze(leaf["text"]) in squeezed else dropped).append(leaf)

    textless: list[dict[str, Any]] = []
    for _path, node in _iter_raw_nodes(structured_data.get("elements") or []):
        has_kids = bool(node.get("Kids"))
        has_text = isinstance(node.get("Text"), str) and node["Text"].strip()
        if has_text or has_kids:
            continue
        # Only real document objects count. Without this filter the walk also
        # visits Font/CharBounds/attributes sub-dicts and reports hundreds of
        # pseudo-leaves, which would hide the one missing bullet that matters.
        if not node.get("Path") and "Bounds" not in node:
            continue
        textless.append(
            {
                "path": str(node.get("Path") or ""),
                "page_number": int(node.get("Page", 0)) + 1,
                "object_id": node.get("ObjectID"),
                "bounds": node.get("Bounds"),
                "has_clip": bool(node.get("HasClip")),
            }
        )

    path_counts = Counter(_container_of(leaf["path"]) for leaf in dropped)
    return {
        "raw_text_leaf_count": len(leaves),
        "normalized_block_count": len(normalized_texts),
        "raw_leaves_present_in_normalized": len(matched),
        "raw_leaves_absent_from_normalized": len(dropped),
        "dropped_by_container": dict(path_counts.most_common()),
        "dropped_leaves": dropped,
        "count_caveat": (
            "`raw_leaves_absent_from_normalized` counts leaves whose exact squeezed text "
            "does not appear anywhere in the normalized layer. Leaves that were MERGED "
            "into another block still count as present, so this number is a conservative "
            "lower bound on normalization loss and must not be compared naively against "
            "the raw-minus-normalized block-count difference."
        ),
        # A Text-less leaf is a DIFFERENT failure class from normalization loss:
        # nothing downstream can recover it, including a larger context window.
        "textless_leaf_count": len(textless),
        "textless_leaves": textless,
        "interpretation": (
            "raw_leaves_absent_from_normalized measures loss introduced by "
            "normalization; textless_leaves measures visible elements with no raw "
            "text extraction at all. They must be reported and remedied separately."
        ),
    }


def _squeeze(text: str) -> str:
    return "".join(str(text).split())


def _container_of(path: str) -> str:
    parts = [part for part in path.split("/") if part]
    return parts[-2] if len(parts) >= 2 else (parts[0] if parts else "?")


class EvidencePod:
    """Bounded read-only access to ONE target's original evidence.

    Lives in the run directory: every crop and measurement is a run artifact,
    so a reviewer can re-inspect exactly what the investigator saw.
    """

    def __init__(
        self,
        target_pdf: Path,
        adobe_json: Path,
        out_dir: Path,
        *,
        normalized: Any | None = None,
    ) -> None:
        self.target_pdf = target_pdf.resolve()
        self.adobe_json = adobe_json.resolve()
        self.out_dir = out_dir
        self.raw = json.loads(self.adobe_json.read_text(encoding="utf-8"))
        self.structured_data = self.raw.get("structured_data") or self.raw
        self.normalized = normalized
        self.page_sizes = _raw_page_sizes(self.structured_data)
        self.page_count = len(self.structured_data.get("pages") or [])
        self.records: list[EvidenceAccessRecord] = []
        self.artifacts: dict[str, Path] = {}
        self._page_images: dict[str, list[Path]] = {}
        self._sequence = 0

    # -- plumbing ------------------------------------------------------------

    def _next_id(self, kind: str) -> str:
        self._sequence += 1
        return f"ev.{kind}.{self._sequence:03d}"

    def _record(self, record: EvidenceAccessRecord) -> dict[str, Any]:
        self.records.append(record)
        return record.model_dump(mode="json")

    def page_pixels(self, page_number: int, source: str = "target") -> tuple[list[Path], int, int]:
        """Render (once per source) and cache full-resolution page PNGs."""
        key = f"{source}:{self.target_pdf.name}"
        if key not in self._page_images:
            prefix = f"{source}_{self.target_pdf.stem}"
            self._page_images[key] = _render_pages(self.target_pdf, self.out_dir, prefix)
        pages = self._page_images[key]
        if not 1 <= page_number <= len(pages):
            raise ValueError(f"page {page_number} outside 1..{len(pages)}")
        from PIL import Image

        with Image.open(pages[page_number - 1]) as image:
            size = image.size
        return pages, size[0], size[1]

    def _pt_to_px(
        self, page_number: int, bbox_pt: list[float], source: str
    ) -> tuple[int, int, int, int, float, float]:
        """Translate PDF-point coordinates into pixel coordinates of the page image."""
        _pages, width_px, height_px = self.page_pixels(page_number, source)
        width_pt, height_pt = self.page_sizes[page_number]
        scale_x, scale_y = width_px / width_pt, height_px / height_pt
        x0, top, x1, bottom = (float(v) for v in bbox_pt)
        return (
            int(round(x0 * scale_x)),
            int(round(top * scale_y)),
            int(round(x1 * scale_x)),
            int(round(bottom * scale_y)),
            scale_x,
            scale_y,
        )

    # -- tools ---------------------------------------------------------------

    def describe_target(self) -> dict[str, Any]:
        """Page count/sizes, source classes, and hashes. No payload, no overview."""
        leaves = _raw_text_leaves(self.structured_data)
        kinds = Counter(_container_of(leaf["path"]) for leaf in leaves)
        return {
            "target_pdf": str(self.target_pdf),
            "target_sha256": _sha256_file(self.target_pdf),
            "adobe_json_path": self.adobe_json.name,
            "adobe_json_sha256": _sha256_file(self.adobe_json),
            "page_count": self.page_count,
            "page_sizes_pt": {str(k): list(v) for k, v in sorted(self.page_sizes.items())},
            "raw_text_leaf_count": len(leaves),
            "raw_leaves_by_container": dict(kinds.most_common()),
            "extended_metadata": self.structured_data.get("extended_metadata"),
            "available_evidence": [
                "page_overview",
                "region_crop(original resolution)",
                "adobe_element_lookup",
                "local_pdf_measurement",
                "coverage_audit",
            ],
            "not_available": {
                "live_adobe_call": "no provider call in offline mode; a cached response is used",
                "normalized_evidence_as_authority": "raw evidence only, see tool contract",
            },
        }

    def inspect_page_overview(self, page_number: int, note: str = "") -> list[Any]:
        """Downscaled whole page: page count, columns, and overall sectioning only."""
        from pydantic_ai import BinaryContent

        evidence_id = self._next_id("overview")
        pages, width_px, height_px = self.page_pixels(page_number)
        source_path = pages[page_number - 1]
        artifact = self.out_dir / f"overview_{evidence_id}_page_{page_number}.png"
        with _open_image(source_path) as image:
            ratio = min(1.0, OVERVIEW_MAX_EDGE / max(image.size))
            resized = image.resize(
                (max(1, int(image.width * ratio)), max(1, int(image.height * ratio))),
                _lanczos(),
            )
            resized.save(artifact)
        self.artifacts[evidence_id] = artifact
        self._record(
            EvidenceAccessRecord(
                evidence_id=evidence_id,
                kind="page_overview",
                provider="pypdfium2",
                tool="inspect_page_overview",
                status="available",
                artifact=artifact.name,
                request={"page": page_number, "note": note},
            )
        )
        header = {
            "evidence_id": evidence_id,
            "kind": "page_overview",
            "page": page_number,
            "source_kind": "target_pdf",
            "source_artifact": source_path.name,
            "target_sha256": _sha256_file(self.target_pdf),
            "page_size_pt": list(self.page_sizes[page_number]),
            "full_page_pixels": [width_px, height_px],
            "downscale_ratio": round(ratio, 4),
            "caution": (
                "downscaled overview: page shape only. Small elements may be invisible; "
                "request inspect_page_region at original resolution before claiming detail."
            ),
        }
        return [
            "Page overview follows in the next part. Machine-readable header:\n"
            + json.dumps(header, indent=1),
            BinaryContent(data=artifact.read_bytes(), media_type="image/png"),
        ]

    def inspect_page_region(
        self,
        page_number: int,
        bbox_pt: list[float],
        source: Literal["target", "render"] = "target",
        note: str = "",
    ) -> list[Any]:
        """ORIGINAL-RESOLUTION crop of one bounded region, keeping its page location.

        `source="render"` is refused in E1 rather than silently substituted: no
        candidate render exists at this checkpoint, and a made-up substitute
        would misrepresent what the reviewer looked at.
        """
        from pydantic_ai import BinaryContent

        evidence_id = self._next_id("region")
        if source == "render":
            reason = "no candidate render exists at the E1 target-understanding checkpoint"
            self._record(
                EvidenceAccessRecord(
                    evidence_id=evidence_id,
                    kind="region_crop",
                    provider="pypdfium2",
                    tool="inspect_page_region",
                    status="unavailable",
                    reason=reason,
                    request={"page": page_number, "bbox_pt": bbox_pt, "source": source},
                )
            )
            return [
                json.dumps(
                    {
                        "evidence_id": evidence_id,
                        "status": "unavailable",
                        "reason": reason,
                        "next": "crop a render only after a Builder step produces one",
                    },
                    indent=1,
                )
            ]
        self._validate_bbox(bbox_pt)
        pages, width_px, height_px = self.page_pixels(page_number, source)
        source_path = pages[page_number - 1]
        x0, top, x1, bottom, scale_x, scale_y = self._pt_to_px(page_number, bbox_pt, source)
        scale = (scale_x + scale_y) / 2
        with _open_image(source_path) as image:
            clipped = (
                max(0, min(x0, image.width)),
                max(0, min(top, image.height)),
                max(1, min(x1, image.width)),
                max(1, min(bottom, image.height)),
            )
            crop = image.crop(clipped)
            artifact = self.out_dir / f"region_{evidence_id}_page_{page_number}.png"
            crop.save(artifact)
        self.artifacts[evidence_id] = artifact
        self._record(
            EvidenceAccessRecord(
                evidence_id=evidence_id,
                kind="region_crop",
                provider="pypdfium2",
                tool="inspect_page_region",
                status="available",
                artifact=artifact.name,
                request={"page": page_number, "bbox_pt": bbox_pt, "source": source, "note": note},
            )
        )
        header = {
            "evidence_id": evidence_id,
            "kind": "region_crop",
            "page": page_number,
            "source_kind": "target_pdf",
            "source_artifact": source_path.name,
            "target_sha256": _sha256_file(self.target_pdf),
            "page_size_pt": list(self.page_sizes[page_number]),
            "requested_bbox_pt": [round(float(v), 3) for v in bbox_pt],
            "crop_pixels": list(crop.size),
            "crop_box_px": list(clipped),
            "full_page_pixels": [width_px, height_px],
            "scale_px_per_pt": round(scale, 4),
            "scale_xy": [round(scale_x, 4), round(scale_y, 4)],
            "location_check": (
                "crop_box_px / full_page_pixels must equal requested_bbox_pt / page_size_pt "
                "within one pixel; this keeps the crop located in the whole page."
            ),
            "is_original_resolution": True,
        }
        return [
            "Region crop follows in the next part. Machine-readable header:\n"
            + json.dumps(header, indent=1),
            BinaryContent(data=artifact.read_bytes(), media_type="image/png"),
        ]

    def inspect_adobe_json(
        self,
        page_number: int | None = None,
        element_path: str | None = None,
        element_id: int | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Read-only slice of the ORIGINAL Adobe response — never a rewritten copy."""
        evidence_id = self._next_id("adobe")
        limit = max(1, min(int(limit), 100))
        seen: set[int] = set()
        nodes: list[dict[str, Any]] = []
        for path, node in _iter_raw_nodes(self.structured_data.get("elements") or []):
            if id(node) in seen:
                continue
            if element_id is not None and node.get("ObjectID") != element_id:
                continue
            if element_path is not None and element_path not in str(node.get("Path") or ""):
                continue
            if page_number is not None and element_id is None and element_path is None:
                if int(node.get("Page", 0)) + 1 != page_number:
                    continue
            seen.add(id(node))
            nodes.append(_verbatim_node(node))
            if len(nodes) >= limit:
                break
        status: EvidenceStatus = "available" if nodes else "unavailable"
        self._record(
            EvidenceAccessRecord(
                evidence_id=evidence_id,
                kind="adobe_element",
                provider="adobe_pdf_extract",
                provider_version=str(
                    (self.structured_data.get("version") or {}).get("json_export") or ""
                )
                or None,
                tool="inspect_adobe_json",
                status=status,
                reason=None if nodes else "no raw element matched the requested selector",
                source_path=element_path,
                request={
                    "page": page_number,
                    "element_path": element_path,
                    "element_id": element_id,
                    "limit": limit,
                },
            )
        )
        return {
            "evidence_id": evidence_id,
            "kind": "adobe_element",
            "source_kind": "adobe_json",
            "source_artifact": self.adobe_json.name,
            "source_sha256": _sha256_file(self.adobe_json),
            "status": status,
            "reason": None if nodes else "no raw element matched the requested selector",
            "verbatim": True,
            "normalization_applied": False,
            "raw_text_leaf_count": len(_raw_text_leaves(self.structured_data)),
            "matched_node_count": len(nodes),
            "nodes": nodes,
            "note": (
                "slices are verbatim raw provider nodes (Text/Bounds/CharBounds/Path/Page/"
                "ObjectID retained). Bullet markers, list labels and table-frame text live "
                "here even when the normalized layer drops them."
            ),
        }

    def measure_local_pdf(
        self,
        page_number: int | None = None,
        include: Literal["all", "chars", "words", "rules"] = "all",
    ) -> dict[str, Any]:
        """Permitted bounded pdfplumber measurements from the same PDF bytes.

        ADR 0002: this is measurement of the local PDF, NOT an analyzer and NOT
        a fallback for an Adobe response. Nothing here is normalized evidence.
        """
        import pdfplumber

        evidence_id = self._next_id("measure")
        measurements: dict[str, Any] = {}
        try:
            with pdfplumber.open(self.target_pdf) as document:
                pages = document.pages
                wanted = (
                    range(1, len(pages) + 1)
                    if page_number is None
                    else [page_number]
                )
                for number in wanted:
                    if not 1 <= number <= len(pages):
                        raise ValueError(f"page {number} outside 1..{len(pages)}")
                    page = pages[number - 1]
                    width, height = float(page.width), float(page.height)
                    entry: dict[str, Any] = {
                        "size_pt": [width, height],
                        "char_count": len(page.chars),
                        "word_count": len(page.extract_words()),
                    }
                    if include in ("all", "chars"):
                        entry["chars"] = [
                            {
                                "text": char["text"],
                                "x0": round(float(char["x0"]), 2),
                                "top": round(float(char["top"]), 2),
                                "x1": round(float(char["x1"]), 2),
                                "bottom": round(float(char["bottom"]), 2),
                                "size": round(float(char.get("size") or 0.0), 2),
                                "fontname": char.get("fontname"),
                                "non_stroking_color": _jsonable_color(
                                    char.get("non_stroking_color")
                                ),
                            }
                            for char in page.chars
                        ]
                    if include in ("all", "words"):
                        entry["words"] = [
                            {
                                "text": word["text"],
                                "x0": round(float(word["x0"]), 2),
                                "top": round(float(word["top"]), 2),
                                "x1": round(float(word["x1"]), 2),
                                "bottom": round(float(word["bottom"]), 2),
                            }
                            for word in page.extract_words()
                        ]
                    if include in ("all", "rules"):
                        entry["rules"] = [
                            _rule_record(index, item, width, height)
                            for index, item in _rule_objects(page)
                        ]
                    measurements[str(number)] = entry
        except Exception as error:  # measurement failure is a recorded outcome
            self._record(
                EvidenceAccessRecord(
                    evidence_id=evidence_id,
                    kind="local_measurement",
                    provider="pdfplumber",
                    tool="measure_local_pdf",
                    status="unavailable",
                    reason=f"pdfplumber measurement failed: {error}",
                    request={"page": page_number, "include": include},
                )
            )
            return {
                "evidence_id": evidence_id,
                "kind": "local_measurement",
                "status": "unavailable",
                "reason": f"pdfplumber measurement failed: {error}",
                "is_analyzer_fallback": False,
            }
        artifact = self.out_dir / f"measurement_{evidence_id}.json"
        artifact.write_text(
            json.dumps(measurements, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        self.artifacts[evidence_id] = artifact
        self._record(
            EvidenceAccessRecord(
                evidence_id=evidence_id,
                kind="local_measurement",
                provider="pdfplumber",
                tool="measure_local_pdf",
                status="available",
                artifact=artifact.name,
                source_path=self.target_pdf.name,
                request={"page": page_number, "include": include},
            )
        )
        return {
            "evidence_id": evidence_id,
            "kind": "local_measurement",
            "source_kind": "target_pdf",
            "source_artifact": self.target_pdf.name,
            "source_sha256": _sha256_file(self.target_pdf),
            "provenance": "local_pdf",
            "method": "pdfplumber_objects",
            "is_analyzer_fallback": False,
            "artifact": artifact.name,
            "pages": measurements,
        }

    def audit_coverage(self) -> dict[str, Any]:
        """Raw-to-normalized loss and missing raw leaves, reported separately."""
        evidence_id = self._next_id("coverage")
        if self.normalized is None:
            self._record(
                EvidenceAccessRecord(
                    evidence_id=evidence_id,
                    kind="coverage_audit",
                    provider="local",
                    tool="audit_coverage",
                    status="unavailable",
                    reason=(
                        "no normalized evidence supplied to the pod; the audit is "
                        "skipped rather than guessing a normalized count"
                    ),
                )
            )
            return {
                "evidence_id": evidence_id,
                "status": "unavailable",
                "reason": "no normalized evidence supplied to the pod",
            }
        texts = [block.text for block in self.normalized.text_blocks]
        audit = audit_evidence_coverage(self.structured_data, texts)
        artifact = self.out_dir / f"coverage_{evidence_id}.json"
        artifact.write_text(json.dumps(audit, ensure_ascii=False, indent=1), encoding="utf-8")
        self.artifacts[evidence_id] = artifact
        self._record(
            EvidenceAccessRecord(
                evidence_id=evidence_id,
                kind="coverage_audit",
                provider="local",
                tool="audit_coverage",
                status="available",
                artifact=artifact.name,
            )
        )
        return {"evidence_id": evidence_id, "status": "available", **audit}

    # -- helpers -------------------------------------------------------------

    def _validate_bbox(self, bbox_pt: list[float]) -> None:
        if len(bbox_pt) != 4:
            raise ValueError("bbox_pt must be [x0, top, x1, bottom]")
        x0, top, x1, bottom = (float(v) for v in bbox_pt)
        if x1 <= x0 or bottom <= top:
            raise ValueError("bbox_pt must satisfy x0 < x1 and top < bottom")
        if (x1 - x0) > MAX_CROP_SIDE_PT * 2 or (bottom - top) > MAX_CROP_SIDE_PT * 2:
            raise ValueError(
                f"region too large to be a region crop (> {MAX_CROP_SIDE_PT * 2}pt): "
                "use inspect_page_overview for whole pages"
            )

    def record_summary(self) -> dict[str, Any]:
        by_status = Counter(record.status for record in self.records)
        return {
            "pod_schema_version": POD_SCHEMA_VERSION,
            "access_count": len(self.records),
            "by_status": dict(by_status),
            "records": [record.model_dump(mode="json") for record in self.records],
        }


def _verbatim_node(node: dict[str, Any]) -> dict[str, Any]:
    keep = (
        "Path",
        "Text",
        "Bounds",
        "CharBounds",
        "Page",
        "ObjectID",
        "Font",
        "TextSize",
        "attributes",
        "HasClip",
        "Lang",
        "filePaths",
    )
    return {key: node[key] for key in keep if key in node}


def _jsonable_color(value: Any) -> Any:
    if value is None or isinstance(value, (int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [round(float(channel), 4) for channel in value]
    return str(value)


def _rule_objects(page: Any) -> list[Any]:
    """Long, thin horizontal line/rectangle objects — the same class ADR 0002 measures."""
    width = float(page.width)
    objects = []
    for index, item in enumerate([*page.lines, *page.rects]):
        x0 = float(item.get("x0") or 0)
        x1 = float(item.get("x1") or 0)
        top = float(item.get("top") or 0)
        bottom = float(item.get("bottom") or top)
        if abs(x1 - x0) < width * 0.5 or abs(bottom - top) > 1.5:
            continue
        objects.append((index, item))
    return objects


def _rule_record(index: int, item: Any, width: float, height: float) -> dict[str, Any]:
    x0 = float(item.get("x0") or 0)
    x1 = float(item.get("x1") or 0)
    top = float(item.get("top") or 0)
    bottom = float(item.get("bottom") or top)
    kind = item.get("object_type")
    return {
        "object_index": index,
        "object_type": "line" if item.get("stroking_color") is not None else (kind or "rect"),
        "bbox_pt": [
            round(min(x0, x1), 2),
            round(min(top, bottom), 2),
            round(max(x0, x1), 2),
            round(max(top, bottom), 2),
        ],
        "bbox_normalized": [
            round(min(x0, x1) / width, 5),
            round(min(top, bottom) / height, 5),
            round(max(x0, x1) / width, 5),
            round(max(top, bottom) / height, 5),
        ],
        "width_pt": round(abs(x1 - x0), 2),
        "thickness_pt": round(abs(bottom - top), 3),
        "linewidth": float(item.get("linewidth") or 0.0),
        "color": _jsonable_color(
            item.get("stroking_color")
            if item.get("stroking_color") is not None
            else item.get("non_stroking_color")
        ),
        "provenance": "local_pdf",
    }


def _open_image(path: Path):
    return Image.open(path).convert("RGB")


def _lanczos():
    return Image.Resampling.LANCZOS


# --- E0: frozen input set ----------------------------------------------------


class FrozenCase(EvidenceModel):
    case_id: str
    target_sha256: str
    adobe_json_sha256: str
    page_count: int = Field(ge=1)
    role: Literal["diagnostic_known", "frozen_blind"]
    label_status: Literal["human_labeled", "pending_human_annotation"]
    notes: str = ""


def freeze_cases(target_pdf: Path, adobe_json: Path, *, role: str, case_id: str) -> FrozenCase:
    """E0: freeze the case identity and hashes BEFORE any model inspection.

    The page count comes from the cached provider response, so a later input
    swap cannot silently change what the frozen case means.
    """
    raw = json.loads(adobe_json.read_text(encoding="utf-8"))
    structured_data = raw.get("structured_data") or raw
    return FrozenCase(
        case_id=case_id,
        target_sha256=_sha256_file(target_pdf),
        adobe_json_sha256=_sha256_file(adobe_json),
        page_count=len(structured_data.get("pages") or []) or 1,
        role=role,  # type: ignore[arg-type]
        label_status="pending_human_annotation",
        notes=(
            "E0 freeze only: no human section/entry/subgroup/reading-order ground truth "
            "exists yet, so the E1 rubric cannot claim accuracy. E1 output is reported as "
            "evidence-linked claims and unresolved items, never as a measured score."
        ),
    )


# --- E1: investigator prompt and scripted rehearsal ---------------------------


INVESTIGATOR_INSTRUCTIONS = (
    "You are a read-only target-structure investigator for a resume-layout "
    "experiment. You never write files, never edit evidence, and never accept "
    "your own work.\n"
    "Work from ORIGINAL evidence: start with the page overviews and the raw "
    "Adobe elements, then request original-resolution region crops or local PDF "
    "measurements only where you have a concrete question. Normalized evidence "
    "is not provided to you and must not be requested as a substitute.\n"
    "Every claim needs an evidence pointer: the evidence_id returned by a tool, "
    "plus the page, bbox, source path, or element id it came from. A claim you "
    "cannot support from evidence goes into `unresolved` with the reason and the "
    "class of evidence gap.\n"
    "Distinguish what you SEE from what you INFER: a visible feature is an "
    "observation; who owns it (section/entry/subgroup/inline role) is a "
    "hypothesis. Report both, never silently merge them.\n"
    "Report raw-absence separately from normalization loss: a bullet visible on "
    "the page but absent from the raw Adobe response is `missing_from_raw_"
    "extraction`, not a normalization problem.\n"
    "Target person facts (names, employers, dates, contacts) may be cited only "
    "as diagnostic evidence for layout inference. They are never candidate data "
    "and must never be proposed as fill values.\n"
    "A confident `ok` that is wrong is a failure. Prefer partial/insufficient_"
    "evidence with explicit unresolved items over a tidy guess."
)


class ScriptedTargetInvestigator:
    """Zero-API rehearsal that still performs real crops and real measurement.

    It plays the part of a careful investigator on the frozen Resume I target:
    inspect the overview, crop a specific region at original resolution, query
    raw Adobe nodes, measure the local PDF, set the host to trace the call, and
    return a draft whose claims point at the evidence it actually collected.
    """

    def __init__(self) -> None:
        self.scripted = 0

    def run(
        self,
        prompt: str,
        *,
        tools: dict[str, Any],
        pod: EvidencePod,
        budget: RunBudget,
        trace: RunTrace,
    ) -> TargetStructureDraft:
        """Rehearsal driver: every call goes through the SAME budget-counted
        traced tools a live investigator would call."""
        self.scripted += 1
        first = tools["inspect_page_overview"](1)
        overview_id = _evidence_id_from_parts(first)
        left_bbox = _bbox_from_overview(pod, column="left")
        second = tools["inspect_page_region"](2, left_bbox)
        region_id = _evidence_id_from_parts(second)
        adobe = tools["inspect_adobe_json"](page_number=2, limit=8)
        measurement = tools["measure_local_pdf"](page_number=2, include="rules")
        coverage = tools["audit_coverage"]()

        refs = {
            "overview": EvidenceRef(
                evidence_id=overview_id,
                kind="page_overview",
                page_number=1,
                source_kind="target_pdf",
                source_path=pod.target_pdf.name,
            ),
            "region": EvidenceRef(
                evidence_id=region_id,
                kind="region_crop",
                page_number=2,
                bbox_pt=left_bbox,
                source_kind="target_pdf",
                source_path=pod.target_pdf.name,
            ),
            "adobe": EvidenceRef(
                evidence_id=adobe["evidence_id"],
                kind="adobe_element",
                page_number=2,
                source_kind="adobe_json",
                source_path="structured_data/elements",
                admissibility="diagnostic_only",
            ),
            "measure": EvidenceRef(
                evidence_id=measurement["evidence_id"],
                kind="local_measurement",
                page_number=2,
                source_kind="target_pdf",
                source_path=pod.target_pdf.name,
            ),
        }
        by_id = {record.evidence_id: record for record in pod.records}

        structure = [
            StructuralRelation(
                claim_id="claim.001",
                relation="reading_order",
                statement=(
                    "The target is two pages; the visible column layout continues across "
                    "the page break, so reading order cannot be decided from page 1 alone."
                ),
                evidence=[refs["overview"], refs["region"]],
                confidence=0.6,
                relation_kind="two_page_two_column",
            ),
            StructuralRelation(
                claim_id="claim.002",
                relation="section_boundary",
                statement=(
                    "A left-column region on page 2 contains a short all-caps label above a "
                    "dense group of short lines; the label is a candidate section heading "
                    "whose owner is still ambiguous."
                ),
                evidence=[refs["region"], refs["adobe"]],
                confidence=0.45,
                relation_kind="candidate_section_heading",
            ),
            StructuralRelation(
                claim_id="claim.003",
                relation="inline_role",
                statement=(
                    "Raw Adobe nodes in that region expose list-label and table-frame leaves "
                    "(Lbl/LI/Table paths) that carry text but have no normalized counterpart."
                ),
                evidence=[refs["adobe"]],
                confidence=0.7,
                relation_kind="raw_only_container_roles",
            ),
        ]
        unresolved = [
            UnresolvedItem(
                item_id="unresolved.001",
                question="Are the short label lines on page 2 section headings or bullets?",
                status="unresolved",
                reason=(
                    "The crop shows the glyphs and the raw nodes show the container paths, "
                    "but neither decides the visual role; a measurement of marker glyphs "
                    "identical to body bullets is absent from both sources."
                ),
                evidence=[refs["region"], refs["adobe"]],
                evidence_gap="ambiguous_relation",
            ),
            UnresolvedItem(
                item_id="unresolved.002",
                question="Which visible page elements exist with no raw text extraction?",
                status="unresolved",
                reason=(
                    "The coverage audit reports raw-to-normalized loss and Text-less leaves "
                    "separately; deciding which Text-less leaves are visually meaningful "
                    "requires the owner's annotation, which does not exist yet."
                ),
                evidence=[
                    EvidenceRef(
                        evidence_id=str(coverage.get("evidence_id", "ev.coverage.unknown")),
                        kind="coverage_audit",
                        source_kind="adobe_json",
                        source_path="structured_data/elements",
                    )
                ],
                evidence_gap="missing_from_raw_extraction",
            ),
            UnresolvedItem(
                item_id="unresolved.003",
                question="What is the exact typography of the page-2 headings?",
                status="unsupported",
                reason=(
                    "Offline mode uses the cached Adobe response and local measurements only; "
                    "no fresh provider typography pass is authorized in this step."
                ),
                evidence=[refs["measure"]],
                evidence_gap="unauthorized_source",
            ),
        ]
        return TargetStructureDraft(
            target_id=pod.target_pdf.stem,
            investigator="scripted",
            structure=structure,
            unresolved=unresolved,
            self_reported=SelfReportedStatus(
                status="partial",
                sections_expected=None,
                sections_identified=1,
                notes=(
                    "Scripted rehearsal: one candidate section label located with evidence; "
                    "owning relations deliberately left unresolved rather than guessed."
                ),
            ),
            evidence_used_by_id=by_id,
        )


def _evidence_id_from_parts(parts: list[Any]) -> str:
    header = json.loads(str(parts[0]).split("\n", 1)[1])
    return str(header["evidence_id"])


def _bbox_from_overview(pod: EvidencePod, *, column: Literal["left", "right"]) -> list[float]:
    """Derive a region bbox in points from the measured page size."""
    width, height = pod.page_sizes[1]
    if column == "left":
        return [round(width * 0.05, 2), round(height * 0.30, 2), round(width * 0.48, 2), round(height * 0.62, 2)]
    return [round(width * 0.52, 2), round(height * 0.30, 2), round(width * 0.96, 2), round(height * 0.62, 2)]


# --- E1 shell -----------------------------------------------------------------


def _pod_tools(pod: EvidencePod, budget: RunBudget, trace: RunTrace) -> list[Any]:
    """The five read-only evidence tools. Every call is budget-counted and traced."""

    def traced(kind: str, fn: Any) -> Any:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            budget.spend_tool(fn.__name__)
            result = fn(*args, **kwargs)
            small = isinstance(result, str) and len(result) < 400
            trace.add(
                agent="main_orchestrator",
                phase="target_understanding",
                action="tool_call",
                tool=fn.__name__,
                input=kwargs or {"args": [str(a)[:120] for a in args]},
                output=result if small else None,
                note=(result[:380] if small else kind),
                persist_output=not small,
            )
            return result

        wrapper.__name__ = fn.__name__
        wrapper.__doc__ = fn.__doc__
        return wrapper

    return [
        traced("describe", pod.describe_target),
        traced("overview", pod.inspect_page_overview),
        traced("region", pod.inspect_page_region),
        traced("adobe", pod.inspect_adobe_json),
        traced("measure", pod.measure_local_pdf),
        traced("coverage", pod.audit_coverage),
    ]


def run_e1(
    target_pdf: Path,
    adobe_json: Path,
    out_dir: Path | None = None,
    *,
    live: bool = False,
    case_role: str = "diagnostic_known",
    case_id: str | None = None,
    normalized_path: Path | None = None,
    budget: RunBudget | None = None,
) -> tuple[Path, str, TargetStructureDraft]:
    """Run the E0 freeze + E1 target-understanding checkpoint.

    Returns (run_dir, terminal_state, draft). The draft is evidence-linked and
    the run is INACTIVE: E1 produces understanding, never an accepted template.
    """
    target_pdf = target_pdf.resolve()
    adobe_json = adobe_json.resolve()
    if not target_pdf.exists():
        raise RuntimeError(f"target PDF not found: {target_pdf}")
    if not adobe_json.exists():
        raise RuntimeError(f"Adobe JSON not found: {adobe_json}")

    out_dir = out_dir or RUNS / datetime.now(UTC).strftime("e_pipeline_e1_%Y%m%dT%H%M%SZ")
    out_dir.mkdir(parents=True, exist_ok=False)

    budget = budget or RunBudget()
    trace = RunTrace(out_dir)
    # No C1 base render exists at this checkpoint, so the injected empty
    # document replaces D's `filled.html` read instead of a fabricated file.
    store = EvidenceStore(out_dir, out_dir, base_html="<html><body></body></html>")
    store.manifest["experiment"] = "e_pipeline_e1"
    store.manifest["pipeline_phase"] = "e0_e1"

    frozen = freeze_cases(
        target_pdf, adobe_json, role=case_role, case_id=case_id or target_pdf.stem
    )
    (out_dir / "frozen_case.json").write_text(
        frozen.model_dump_json(indent=2), encoding="utf-8"
    )
    store.record_state("e0_frozen", f"case={frozen.case_id} sha={frozen.target_sha256[:12]}")
    trace.add(
        agent="shell",
        phase="e0",
        action="case_frozen",
        output=frozen.model_dump(mode="json"),
    )

    shutil.copy2(target_pdf, out_dir / "target.pdf")
    shutil.copy2(adobe_json, out_dir / "adobe_raw.json")
    normalized = None
    if normalized_path is not None and normalized_path.exists():
        from app.template_analysis.commercial.models import NormalizedLayoutEvidence

        normalized = NormalizedLayoutEvidence.model_validate_json(
            normalized_path.read_text(encoding="utf-8")
        )
        shutil.copy2(normalized_path, out_dir / "enriched_evidence.json")

    pod = EvidencePod(target_pdf, adobe_json, out_dir, normalized=normalized)
    (out_dir / "evidence_pod_descriptor.json").write_text(
        json.dumps(pod.describe_target(), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    pod_tools = _pod_tools(pod, budget, trace)
    tools_by_name = {tool.__name__: tool for tool in pod_tools}
    if live:
        draft = _run_live_investigator(pod, pod_tools, budget, trace)
    else:
        draft = ScriptedTargetInvestigator().run(
            "", tools=tools_by_name, pod=pod, budget=budget, trace=trace
        )

    draft.evidence_used_by_id = {record.evidence_id: record for record in pod.records}
    (out_dir / "structure_draft.json").write_text(
        draft.model_dump_json(indent=2), encoding="utf-8"
    )
    trace.add(
        agent=draft.investigator,
        phase="target_understanding",
        action="structure_draft",
        output=draft.model_dump(mode="json"),
        persist_output=True,
    )

    terminal = check_e1_stop_gates(draft, pod)
    store.manifest["terminal_state"] = terminal
    store.manifest["target_sha256"] = frozen.target_sha256
    store.manifest["adobe_json_sha256"] = frozen.adobe_json_sha256
    store.manifest["evidence_access"] = pod.record_summary()
    store.manifest["draft_schema_version"] = DRAFT_SCHEMA_VERSION
    store.manifest["investigator"] = draft.investigator
    store.manifest["self_reported_status"] = draft.self_reported.status
    store.manifest["budget"] = budget.to_json()
    store.manifest["versions"] = [
        {
            "id": "structure_draft_v1",
            "path": "structure_draft.json",
            "sha256": _sha256_file(out_dir / "structure_draft.json"),
            "note": "E1 evidence-linked structure draft (understanding only, no template)",
        }
    ]
    store.record_state(terminal, f"investigator={draft.investigator}")
    (out_dir / "manifest.json").write_text(
        json.dumps(store.manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    trace.save()
    _write_e1_report(out_dir, frozen, draft, pod, budget, terminal)
    return out_dir, terminal, draft


def check_e1_stop_gates(draft: TargetStructureDraft, pod: EvidencePod) -> str:
    """E1 stop conditions (§8): a confident wrong `ok` is a failure, not a pass.

    A structural CLAIM asserts support, so it must cite evidence that was
    actually collected and available. An UNRESOLVED item documents a gap, so it
    may cite the failed attempt itself; it is instead required to carry a
    reason and an evidence-gap class (enforced by the type).
    """
    if not draft.structure and draft.self_reported.status == "ok":
        return "needs_owner_review"
    available = {record.evidence_id for record in pod.records if record.status == "available"}
    collected = {record.evidence_id for record in pod.records}
    unsupported_claims = [
        ref.evidence_id
        for relation in draft.structure
        for ref in relation.evidence
        if ref.evidence_id not in available
    ]
    if unsupported_claims:
        return "needs_owner_review"
    invented = [
        ref.evidence_id
        for item in draft.unresolved
        for ref in item.evidence
        if ref.evidence_id not in collected
    ]
    if invented:
        # An unresolved item may document a failed attempt, never cite evidence
        # that was never requested at all.
        return "needs_owner_review"
    if draft.self_reported.status == "ok":
        # `ok` needs `confirmed_by_shell` claims; nothing confirms them in E1.
        if not any(r.status == "confirmed_by_shell" for r in draft.structure):
            return "needs_owner_review"
    return "awaiting_owner_review"


def _run_live_investigator(
    pod: EvidencePod, pod_tools: list[Any], budget: RunBudget, trace: RunTrace
) -> TargetStructureDraft:
    """One bounded live investigator request. Requires explicit authorization."""
    from pydantic_ai import Agent

    from tests.experiments.d_pipeline import _live_model

    model = _live_model()
    agent = Agent(
        model,
        output_type=TargetStructureDraft,
        name="target_investigator",
        instructions=INVESTIGATOR_INSTRUCTIONS,
        tools=pod_tools,
    )
    if budget.remaining_model_requests() < 1:
        raise CheckpointBudgetExceeded("global model budget exhausted before investigator")
    prompt = (
        "Investigate this target's structure. Machine-readable target description:\n"
        + json.dumps(pod.describe_target(), ensure_ascii=False, indent=1)
        + "\nUse your read-only tools for every claim. Cite evidence ids in `evidence`."
    )
    result = agent.run_sync(prompt, usage_limits=_limits(budget))
    _record_usage(budget, trace, "target_investigator", "target_understanding", result)
    return result.output


def _write_e1_report(
    out_dir: Path,
    frozen: FrozenCase,
    draft: TargetStructureDraft,
    pod: EvidencePod,
    budget: RunBudget,
    terminal: str,
) -> None:
    records = pod.records
    table = "\n".join(
        f"| `{r.evidence_id}` | {r.kind} | {r.status} | {r.artifact or r.source_path or '-'} |"
        f" {r.reason or ''} |"
        for r in records
    ) or "| - | - | - | - | - |"
    claims = "\n".join(
        f"| `{c.claim_id}` | {c.relation} | {c.confidence:.2f} | {c.status} | "
        f"{', '.join(ref.evidence_id for ref in c.evidence) or '-'} | {c.statement} |"
        for c in draft.structure
    ) or "| - | - | - | - | - | - |"
    unresolved = "\n".join(
        f"| `{u.item_id}` | {u.status} | {u.evidence_gap} | {u.question} | "
        f"{', '.join(ref.evidence_id for ref in u.evidence) or '-'} |"
        for u in draft.unresolved
    ) or "| - | - | - | - | - |"
    (out_dir / "REPORT.md").write_text(
        f"""# Pipeline E1 target-understanding run — {out_dir.name}

- Case: `{frozen.case_id}` (role `{frozen.role}`, labels `{frozen.label_status}`)
- Target sha256: `{frozen.target_sha256}`
- Adobe response sha256: `{frozen.adobe_json_sha256}`
- Terminal state: **{terminal}**
- Investigator: `{draft.investigator}`; self-reported: `{draft.self_reported.status}`
- Structure version: `structure_draft_v1` (INACTIVE — understanding only, no template)
- Model requests: {budget.model_requests}/{budget.max_model_requests};
  tool calls: {budget.tool_calls}/{budget.max_tool_calls}
- Trace: `trace.json`; evidence pod: `evidence_pod_descriptor.json`;
  draft: `structure_draft.json`

## Evidence access (every attempt, including failures)

| evidence_id | kind | status | artifact/source | reason |
| --- | --- | --- | --- | --- |
{table}

## Structure claims (each with evidence pointers)

| claim | relation | confidence | status | evidence | statement |
| --- | --- | --- | --- | --- | --- |
{claims}

## Unresolved / unsupported

| item | status | evidence gap | question | evidence |
| --- | --- | --- | --- | --- |
{unresolved}

## What this run does and does not establish

Establishes: the bounded evidence-access paths (overview, original-resolution
region crop, verbatim Adobe-node lookup, permitted local measurement, coverage
audit) run offline against a real target; every structural claim and every
unresolved item carries an evidence pointer; raw-versus-normalized loss and
missing raw extraction are reported as separate classes.

Does **not** establish: any fidelity or accuracy score. There is no
human-annotated ground truth yet (`{frozen.label_status}`), so E1 cannot and
does not claim correct or incorrect structure. No live model call was made and
no owner visual acceptance is implied. The reconstruction of any target text is
diagnostic only and is never candidate data.

## Authority boundary

`PIPELINE_E_PLAN.md` §3.6: a model reading target page images or raw target
text is BEYOND ADR 0001/0006. This run is experiment-only evidence; it changes
no product contract, no ADR, no roadmap item, and no production code.
""",
        encoding="utf-8",
    )


# ===========================================================================
# E2 — See -> Measure -> Attribute -> Repair -> Re-render (PIPELINE_E_PLAN.md §8)
# ===========================================================================

E2_SCHEMA_VERSION = "pipeline-e-e2-state/1"
# Tolerances are re-exported from the canonical C2 rendered-comparison contract
# (documented BEFORE evaluation in c2_docx_build.py; never tuned afterwards).
E2_IMPROVEMENT_TOLERANCE_PT = TOLERANCE_PT["local_gap"]

# Bounded repair vocabulary (plan §8.4): each layer is one typed mutation; a
# model cannot express arbitrary edits and cannot touch candidate facts.
RepairLayer = Literal["plan_entry_gap", "state_style", "no_op"]


class MeasurementRequest(EvidenceModel):
    """A typed question about ONE region/property/relationship (plan §8.2).

    Role-aligned anchors differ between documents (target and candidate text
    differ), so each side carries its own verbatim line-prefix anchor pair:
    `from_text`/`to_text` on the TARGET, `render_from_text`/`render_to_text`
    on the CURRENT render (plan §9 rule 3: match semantic roles, never row N
    to row N)."""

    request_id: str
    metric: Literal["role_gap"]
    page: int = Field(ge=1)
    from_text: str = Field(min_length=1)
    to_text: str = Field(min_length=1)
    render_from_text: str = Field(min_length=1)
    render_to_text: str = Field(min_length=1)
    units: Literal["pt"] = "pt"
    region_id: str | None = None


class MeasurementResult(EvidenceModel):
    """Raw page-local anchors from the ACTUAL final PDF + a typed status (§8.2).
    VLM confidence cannot override these statuses."""

    request_id: str
    status: Literal["confirmed", "falsified", "ambiguous", "evidence_missing", "not_measurable"]
    target_value_pt: float | None = None
    current_value_pt: float | None = None
    delta_pt: float | None = None
    target_evidence: list[dict[str, Any]] = Field(default_factory=list)
    current_evidence: list[dict[str, Any]] = Field(default_factory=list)
    method: str = ""
    warnings: list[str] = Field(default_factory=list)
    reason: str | None = None


class DefectFinding(EvidenceModel):
    """Observation-first localized finding bound to exact artifact versions
    (plan §8.1). An observation without exact versions is invalid. The
    reviewer separates what it SEES from what it SUSPECTS; it never decides
    root cause and never approves delivery."""

    finding_id: str
    target_version: str
    render_version: str
    page: int = Field(ge=1)
    region: str
    observation: str
    suspected_dimension: str
    proposed_cause: str | None = None  # hypothesis only; attribution decides
    requested_measurement: MeasurementRequest
    severity: Literal["low", "medium", "high"]
    confidence: float = Field(ge=0.0, le=1.0)
    reviewer: Literal["scripted", "llm"]


class AttributionRecord(EvidenceModel):
    """One confirmed mismatch's causal trace (plan §8.3). The observation
    survives even when its original causal hypothesis is falsified."""

    finding_id: str
    render_version: str
    measurement_request_id: str
    attribution: Literal[
        "target_evidence_missing",
        "target_understanding",
        "template_compilation",
        "candidate_binding",
        "render_plan",
        "renderer",
        "measurement_failure",
        "not_measurable",
        "no_defect",
    ]
    hypothesis_status: Literal["confirmed", "rejected", "unresolved", "not_tested"]
    repair_owner: Literal["builder", "investigator", "reviewer", "none"]
    evidence: list[str] = Field(default_factory=list)
    reason: str


class RepairProposal(EvidenceModel):
    """A typed, scoped repair (plan §8.4). The deterministic shell validates
    authorization, references, layer scope, and candidate safety BEFORE any
    application; candidate facts are never modifiable."""

    finding_id: str
    base_render_version: str
    layer: Literal["plan_entry_gap", "state_style", "no_op"]
    section_node_id: str
    gap_delta_pt: float = Field(default=0.0)
    style_updates: dict[str, str] = Field(default_factory=dict)
    rationale: str
    agent: Literal["scripted", "llm"]

    @model_validator(mode="after")
    def bounded_mutation(self) -> "RepairProposal":
        if self.layer == "no_op":
            return self
        if self.layer == "plan_entry_gap" and self.gap_delta_pt == 0.0:
            raise ValueError("plan_entry_gap repair requires a non-zero gap_delta_pt")
        if self.layer == "state_style" and not self.style_updates:
            raise ValueError("state_style repair requires style_updates")
        for key in self.style_updates:
            if key.startswith("leaf_"):
                raise ValueError("candidate fact mutation is forbidden (plan §5.1)")
        return self


class RenderVersion(EvidenceModel):
    """One versioned render with its gate results and promotion state."""

    version_id: str
    html_sha256: str
    pdf_sha256: str
    page_count: int = Field(ge=1)
    hard_gates_passed: bool
    promoted: bool = False
    note: str = ""


class E2LoopRecord(EvidenceModel):
    """The resumable E2 state (plan §14): versions, findings, attributions,
    measurement results, repair attempts, attempted strategies, budget, and
    best-valid selection. Persisted to the run manifest and a typed JSON."""

    schema_version: Literal["pipeline-e-e2-state/1"] = E2_SCHEMA_VERSION
    target_id: str
    render_versions: list[RenderVersion] = Field(default_factory=list)
    best_render_version: str | None = None
    findings: list[DefectFinding] = Field(default_factory=list)
    measurement_results: list[MeasurementResult] = Field(default_factory=list)
    attributions: list[AttributionRecord] = Field(default_factory=list)
    repair_attempts: list[dict[str, Any]] = Field(default_factory=list)
    attempted_strategies: list[str] = Field(default_factory=list)
    action_fingerprints: list[str] = Field(default_factory=list)
    open_findings: list[str] = Field(default_factory=list)
    budget_state: dict[str, Any] = Field(default_factory=dict)
    summary: dict[str, Any] = Field(default_factory=dict)


# --- reviewer agent (live + scripted) ---------------------------------------

REVIEWER_INSTRUCTIONS = (
    "You are the independent Visual Reviewer of a resume-layout experiment.\n"
    "You inspect the TARGET and the RENDERED page crops you are given, plus\n"
    "measured facts you explicitly requested earlier. You report LOCALIZED\n"
    "visible differences only.\n"
    "Hard boundaries: you never decide the root cause layer; `proposed_cause`\n"
    "is a hypothesis for verification, never a verdict. You never approve\n"
    "delivery. You never modify anything. Every finding binds the exact\n"
    "target and render version strings you were given, and carries one\n"
    "typed measurement request the shell must execute on the final PDF.\n"
    "Coordinates you report are page-local points; text anchors are VERBATIM\n"
    "line-prefix strings that deterministic measurement can locate in the\n"
    "final PDF.\n"
    "Target person facts are diagnostic evidence only; they never become\n"
    "candidate content."
)


class ScriptedReviewer:
    """Offline observation-first reviewer rehearsal: one real localized
    finding per round from the real target/render pair, with a typed
    measurement request. Plays the scout role the plan assigns — no verdict,
    no approval, no root-cause claim."""

    def __init__(self) -> None:
        self.scripted = 0

    def run(
        self,
        prompt: str,
        *,
        finding_id: str,
        target_version: str,
        render_version: str,
        page: int,
        budget: RunBudget,
        trace: RunTrace,
        agent_name: str = "visual_reviewer",
    ) -> list[DefectFinding]:
        self.scripted += 1
        budget.spend_model(agent_name)
        # The scripted scout inspects the same real pair a live reviewer sees:
        # the orchestrator prompt carries the current target/render anchors and
        # the known role-aligned divergence; the finding stays observation-first
        # and cites the requested measurement, not a cause.
        finding = DefectFinding(
            finding_id=finding_id,
            target_version=target_version,
            render_version=render_version,
            page=page,
            region=prompt_region(prompt),
            observation=prompt_observation(prompt),
            suspected_dimension="role_gap",
            requested_measurement=prompt_request(prompt, finding_id),
            severity="medium",
            confidence=0.6,
            reviewer="scripted",
        )
        trace.add(
            agent=agent_name,
            phase="review",
            action="finding",
            output=finding.model_dump(mode="json"),
            persist_output=True,
        )
        return [finding]


def _live_reviewer_findings(
    page_images: list[Any],
    finding_id: str,
    target_version: str,
    render_version: str,
    page: int,
    node_inventory: str,
    budget: RunBudget,
    trace: RunTrace,
) -> list[DefectFinding]:
    """One bounded live reviewer request over the side-by-side crop/overview.
    Requires explicit authorization (never called offline)."""
    from pydantic_ai import Agent, BinaryContent

    from tests.experiments.d_pipeline import _live_model

    model = _live_model()
    agent = Agent(
        model,
        output_type=list[DefectFinding],
        name="visual_reviewer",
        instructions=REVIEWER_INSTRUCTIONS,
    )
    if budget.remaining_model_requests() < 1:
        raise CheckpointBudgetExceeded("budget exhausted before visual reviewer")
    result = agent.run_sync(
        [
            "Compare the TARGET and RENDER images for this page. Report localized "
            "observation-first findings. Each finding must carry the exact "
            f"target_version={target_version!r} render_version={render_version!r} "
            f"page={page}, a region id from the node inventory, an observation, a "
            "suspected dimension, and one typed measurement request whose text "
            "anchors are verbatim line prefixes of the render PDF.\nNode inventory:\n"
            + node_inventory,
            *[BinaryContent(data=image.read_bytes(), media_type="image/png") for image in page_images],
        ],
        usage_limits=_limits(budget),
    )
    _record_usage(budget, trace, "visual_reviewer", "review", result)
    for finding in result.output:
        finding.finding_id = finding_id
        finding.target_version = target_version
        finding.render_version = render_version
        finding.reviewer = "llm"
    return result.output


# --- measurement controller (deterministic) ---------------------------------


class MeasureController:
    """Executes typed measurement requests against ACTUAL final PDF bytes
    (plan §9): pdfplumber word/rule geometry, page-local, deduplicated rules,
    page numbers always carried. Nothing synthetic stands in for the measured
    object."""

    def __init__(self, pod: "DualSourcePod", budget: RunBudget, trace: RunTrace) -> None:
        self.pod = pod
        self.budget = budget
        self.trace = trace

    def execute(self, request: MeasurementRequest, *, current_pdf: Path) -> MeasurementResult:
        self.budget.spend_tool("compare_pdf_geometry")
        target_rows = self.pod.pdf_line_rows(self.pod.target_pdf, request.page)
        current_rows = self.pod.pdf_line_rows(current_pdf, request.page)
        target_value = _role_gap_from_rows(target_rows, request.from_text, request.to_text)
        current_value = _role_gap_from_rows(
            current_rows, request.render_from_text, request.render_to_text
        )
        if target_value is None or current_value is None:
            result = MeasurementResult(
                request_id=request.request_id,
                status="evidence_missing",
                reason=(
                    "role anchor absent from the final PDF word rows "
                    f"(target={'ok' if target_value is not None else 'missing'}, "
                    f"current={'ok' if current_value is not None else 'missing'})"
                ),
                method="pdfplumber_word_rows_role_gap/1",
                warnings=["PDF text order may concatenate columns; anchors are verbatim line prefixes"],
            )
        else:
            result = MeasurementResult(
                request_id=request.request_id,
                status="confirmed",
                target_value_pt=target_value,
                current_value_pt=current_value,
                delta_pt=round(current_value - target_value, 3),
                method="pdfplumber_word_rows_role_gap/1",
                warnings=[
                    "coordinates are page-local pt; page number carried",
                    "rules were deduplicated before counting; word rows are raw boxes",
                ],
            )
        self.trace.add(
            agent="measure_controller",
            phase="measure",
            action="measurement",
            tool="compare_pdf_geometry",
            output=result.model_dump(mode="json"),
            persist_output=True,
        )
        return result


def _role_gap_from_rows(
    rows: list[dict[str, Any]], from_text: str, to_text: str
) -> float | None:
    """Page-local top distance between two verbatim line-prefix anchors.
    Semantic ROLE matching: first line whose joined text starts with the
    anchor; never row N to row N."""
    tops: dict[str, float] = {}
    for row in rows:
        text = row["text"]
        if text.startswith(from_text) and from_text not in tops:
            tops[from_text] = row["top"]
        if text.startswith(to_text) and to_text not in tops:
            tops[to_text] = row["top"]
    if from_text not in tops or to_text not in tops:
        return None
    return round(tops[to_text] - tops[from_text], 3)


# --- E2 shell ----------------------------------------------------------------


def run_e2(
    target_pdf: Path,
    out_dir: Path | None = None,
    *,
    live: bool = False,
    max_repair_attempts: int = 2,
    budget: RunBudget | None = None,
) -> tuple[Path, str, dict[str, Any]]:
    """Run the E2 See/Measure/Attribute/Repair/Re-render vertical slice.

    Returns (run_dir, terminal_state, record_dict). Normal exits:
    ``delivered_pending_owner`` (gates green) and ``budget_exhausted`` (never
    success; state persisted for a later resumable run). Operational failures
    abort with a description of the failed operation and never classify the
    template as unsupported. The offline path performs REAL Chrome renders and
    REAL pdfplumber measurement with zero model calls.
    """
    from tests.experiments.c2_candidates import candidate_resume_E
    from tests.experiments.c2_pipeline import compile_layout_state, run_flow_probe
    from tests.experiments.c2_plan import compile_render_plan
    from tests.experiments.c2_html import render_html
    from tests.experiments import c2_renderer as c2r
    from tests.experiments.c2_state import validate_layout_state, state_bytes

    target_pdf = target_pdf.resolve()
    if not target_pdf.exists():
        raise RuntimeError(f"target PDF not found: {target_pdf}")

    out_dir = out_dir or RUNS / datetime.now(UTC).strftime("e_pipeline_e2_%Y%m%dT%H%M%SZ")
    out_dir.mkdir(parents=True, exist_ok=False)

    budget = budget or RunBudget(max_model_requests=4, max_tool_calls=48)
    trace = RunTrace(out_dir)
    store = EvidenceStore(out_dir, out_dir, base_html="<html><body></body></html>")
    store.manifest["experiment"] = "e_pipeline_e2"
    store.manifest["pipeline_phase"] = "e2"

    def abort(operation: str, error: Exception) -> tuple[Path, str, dict[str, Any]]:
        """Operational abort: describes the failed operation; never 'unsupported'."""
        record = _empty_record("unknown", budget)
        record["terminal_state"] = "operational_abort"
        record["abort"] = {"operation": operation, "error": str(error)}
        store.manifest["terminal_state"] = "operational_abort"
        store.manifest["abort"] = record["abort"]
        trace.add(agent="shell", phase="operational", action="abort", note=f"{operation}: {error}")
        (out_dir / "e2_state.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        store.record_state("operational_abort", f"{operation}: {error}")
        (out_dir / "manifest.json").write_text(
            json.dumps(store.manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        trace.save()
        return out_dir, "operational_abort", record

    # 0. availability of the required local service (renderer) up front.
    try:
        environment = pinned_export_environment({})
    except Exception as error:
        return abort("pinned_chrome_environment", error)

    # 1. Target evidence: immutable cached Adobe response + coverage audit
    #    (raw vs normalized loss reported separately, never merged).
    target_cache_dir = RUNS / "target_cache" / _sha256_file(target_pdf)
    raw_path = target_cache_dir / "adobe_raw.json"
    normalized_path = target_cache_dir / "enriched_evidence.json"
    if not raw_path.exists() or not normalized_path.exists():
        return abort(
            "target_evidence_cache",
            RuntimeError(
                "no cached Adobe response for this target under "
                "tests/experiments/runs/target_cache/; E2 makes no live provider "
                "call itself (ADR 0002; only cached evidence is read)"
            ),
        )
    shutil.copy2(raw_path, out_dir / "adobe_raw.json")
    shutil.copy2(normalized_path, out_dir / "enriched_evidence.json")
    blind_raw = json.loads(raw_path.read_text(encoding="utf-8"))
    structured = raw_path  # verbatim raw provider response is copied above
    normalized: Any = None
    try:
        from app.template_analysis.commercial.models import NormalizedLayoutEvidence

        normalized = NormalizedLayoutEvidence.model_validate_json(
            normalized_path.read_text(encoding="utf-8")
        )
    except Exception as error:
        return abort("normalized_evidence_load", error)

    pod = DualSourcePod(target_pdf, structured, out_dir, render_pdf=None, normalized=normalized)
    coverage = pod.audit_coverage()
    target_id = f"target-{target_pdf.stem}-v1"

    # 2. Presentation compilation through the canonical C2 chain (existing
    #    components; DOCX stays out of this lane).
    try:
        evidence_obj = normalized
        from tests.experiments.a_pipeline import build_format_summary, _analyze_target

        summary = build_format_summary(evidence_obj, json.loads(raw_path.read_text(encoding="utf-8")), target_pdf)
        state = compile_layout_state(target_pdf, summary, evidence=evidence_obj, provider_name="adobe")
    except Exception as error:
        return abort("compile_layout_state", error)
    state_violations = validate_layout_state(state)
    (out_dir / "c2_layout_state.json").write_bytes(state_bytes(state))

    # 3. Candidate content: the frozen reviewed render context (author-
    # segmented, verbatim source coverage verified by the canonical coverage
    # walk in the C2 lane; E2 adds no second segmentation).
    candidate = candidate_resume_E()
    probe = run_flow_probe(state, candidate)
    (out_dir / "flow_probe.json").write_text(
        json.dumps(probe, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    # Frozen C1 baseline target for the privacy gate (E2's candidate content is
    # resume E's; the canonical baseline pair is E->F).
    frozen_dir = RUNS / "c_pipeline_D_to_E_20260910T195515Z"
    if not (frozen_dir / "target.pdf").exists():
        return abort("frozen_c1_baseline", FileNotFoundError(str(frozen_dir / "target.pdf")))
    privacy_target = frozen_dir / "target.pdf"

    best: dict[str, Any] = {
        "version_id": None,
        "gap": None,
        "gates": None,
    }
    versions: list[RenderVersion] = []
    findings: list[DefectFinding] = []
    measurement_results: list[MeasurementResult] = []
    attributions: list[AttributionRecord] = []
    repair_attempts: list[dict[str, Any]] = []
    attempted_strategies: list[str] = []
    fingerprints: list[str] = []
    open_findings: list[str] = []
    counter = {"render": 0, "finding": 0, "request": 0}

    def render_version(gap_delta: float, note: str) -> tuple[RenderVersion, Path, dict[str, Any]]:
        """Render one whole document version through the canonical chain and
        run the delivery gates. Candidates are INACTIVE until the shell
        promotes; nothing is overwritten."""
        from tests.experiments.c2_plan import compile_render_plan
        from tests.experiments.c2_html import render_html
        from tests.experiments import c2_renderer as c2r

        budget.spend_tool("render_and_checkpoint")
        plan = compile_render_plan(state, candidate)
        sec = next(s for s in plan.sections if s.node_id == SECTION_NODE_ID)
        base_gap = sec.inter_entry_gap_above_pt or 0.0
        plan.sections = [
            s.model_copy(update={"inter_entry_gap_above_pt": round(base_gap + gap_delta, 3)})
            if s.node_id == SECTION_NODE_ID
            else s
            for s in plan.sections
        ]
        html = render_html(state, plan)
        html_path = out_dir / f"render_{len(versions) + 1}.html"
        html_path.write_text(html, encoding="utf-8")
        pdf_path = out_dir / f"render_{len(versions) + 1}.pdf"
        pdf = _export_pinned_html_to_pdf(html_path, pdf_path, environment)
        pages = _render_pages(pdf, out_dir, f"render_{len(versions) + 1}")
        second = out_dir / f"render_{len(versions) + 1}_second.pdf"
        _export_pinned_html_to_pdf(html_path, second, environment)
        second_pages = _render_pages(second, out_dir, f"render_{len(versions) + 1}_second")
        stability = c2r._line_stability(pdf, second)
        content = c2r.content_gate(plan, html, pdf)
        privacy = c2r.privacy_gate(plan, privacy_target, html, pdf)
        structure = c2r.structure_gate(plan, state, html, pdf)
        blank = c2r.blank_page_gate(pdf)
        accounting = c2r.candidate_accounting_gate(plan, content)
        header_scaffold = derive_header_scaffold(target_pdf, summary)
        body_scaffold = derive_body_scaffold(target_pdf, summary, header_scaffold=header_scaffold)
        bullet_tiers = derive_body_tier_targets(
            target_pdf,
            float(body_scaffold.entry.left_x0_pt) if body_scaffold.entry else state.page.margin_left_pt,
        )
        shape = c2r.content_shape_verification(state, plan, body_scaffold, bullet_tiers, html, summary, pdf)
        gates = {
            # Canonical determinism contract (c2_renderer.determinism_gate):
            # page-RASTER hash equality + per-line stability; PDF bytes differ
            # only in the export timestamp, which is inherent Chrome
            # nondeterminism (documented in the C2-0cS record).
            "deterministic_render": all(
                c2r._sha256(left) == c2r._sha256(right)
                for left, right in zip(pages, second_pages)
            )
            and stability["passed"],
            "no_target_candidate_facts": privacy["passed"],
            "section_order_matches_state": structure["section_order_matches_state"],
            "no_blank_page": blank["passed"],
            "candidate_content_accounting": accounting["passed"],
            "content_shapes_match_evidence": shape["passed"],
            "content_gate": content["passed"],
        }
        version = RenderVersion(
            version_id=f"render-{target_pdf.stem}-v{len(versions) + 1}",
            html_sha256=_sha256_file(html_path),
            pdf_sha256=_sha256_file(pdf_path),
            page_count=len(pages),
            hard_gates_passed=all(gates.values()),
            note=note,
        )
        versions.append(version)
        store.register_version(
            version.version_id,
            pdf_path,
            f"{note}; gates={'pass' if version.hard_gates_passed else 'fail'}",
        )
        (out_dir / f"hard_gates_{version.version_id}.json").write_text(
            json.dumps({"passed": version.hard_gates_passed, "gates": gates}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        trace.add(
            agent="shell",
            phase="render",
            action="render_version",
            output=version.model_dump(mode="json"),
            note=note,
        )
        return version, pdf, gates

    def measure_and_attribute(finding: DefectFinding, pdf: Path) -> tuple[MeasurementResult, AttributionRecord]:
        counter["request"] += 1
        request = finding.requested_measurement.model_copy(
            update={"request_id": f"measure-{counter['request']:03d}"}
        )
        result = MeasureController(pod, budget, trace).execute(request, current_pdf=pdf)
        measurement_results.append(result)
        trace.add(
            agent="attribution_investigator",
            phase="attribute",
            action="trace",
            input={"finding": finding.finding_id, "request": request.request_id},
            note="raw target evidence -> structure -> template slot -> candidate binding -> RenderPlan -> DOM/CSS -> final PDF object",
        )
        # Deterministic attribution on the measured delta: the reviewer's
        # proposed_cause is a hypothesis and is recorded separately; the shell
        # decides the repair owner from the measurement, not from the model.
        if result.status == "confirmed" and result.delta_pt is not None:
            if abs(result.delta_pt) <= E2_IMPROVEMENT_TOLERANCE_PT:
                attribution = AttributionRecord(
                    finding_id=finding.finding_id,
                    render_version=finding.render_version,
                    measurement_request_id=request.request_id,
                    attribution="no_defect",
                    hypothesis_status="rejected",
                    repair_owner="none",
                    evidence=[result.request_id],
                    reason="measured role gap matches the target within the documented tolerance",
                )
            else:
                attribution = AttributionRecord(
                    finding_id=finding.finding_id,
                    render_version=finding.render_version,
                    measurement_request_id=request.request_id,
                    attribution="template_compilation",
                    hypothesis_status="confirmed",
                    repair_owner="builder",
                    evidence=[result.request_id],
                    reason=(
                        "the compiled plan carries the declared gap; the measured final-PDF "
                        "object differs from the target's measured role gap beyond tolerance"
                    ),
                )
        else:
            attribution = AttributionRecord(
                finding_id=finding.finding_id,
                render_version=finding.render_version,
                measurement_request_id=request.request_id,
                attribution="measurement_failure",
                hypothesis_status="unresolved",
                repair_owner="reviewer",
                evidence=[result.request_id],
                reason="the measurement did not bind to a final-PDF object; re-verify before repairing",
            )
        attributions.append(attribution)
        trace.add(
            agent="attribution_investigator",
            phase="attribute",
            action="attribution",
            output=attribution.model_dump(mode="json"),
        )
        return result, attribution

    # 4. First render (v1) — the owner-reviewable baseline.
    v1, v1_pdf, v1_gates = render_version(0.0, "first render (plan-measured gaps)")
    target_frozen = freeze_cases(target_pdf, raw_path, role="diagnostic_known", case_id=target_pdf.stem)
    (out_dir / "frozen_case.json").write_text(target_frozen.model_dump_json(indent=2), encoding="utf-8")
    if not v1.hard_gates_passed:
        # A broken FIRST render is a confirmed material defect at the template
        # layer; the loop treats it as the finding source instead of aborting.
        pass

    # 5. Review -> Measure -> Attribute -> Repair -> Re-render loop.
    reviewer = _live_reviewer_findings if live else None
    terminal = "budget_exhausted"
    for attempt in range(1, max_repair_attempts + 1):
        if budget.remaining_model_requests() < 1:
            trace.add(agent="shell", phase="loop", action="budget_exhausted", note="before review")
            break
        # See: independent reviewer on the CURRENT versions (the CURRENT
        # best hard-gate-valid render; a rejected repair keeps the previous
        # best as current). The reviewer sees target and render page images;
        # it never sees the builder rationale.
        current_index = len(versions)
        current_version = versions[-1]
        current_pdf = _render_pdf_of(out_dir, current_index)
        # See: independent reviewer on the CURRENT versions.
        counter["finding"] += 1
        prompt = (
            f"TARGET {target_frozen.case_id} sha={target_frozen.target_sha256[:12]} "
            f"RENDER {current_version.version_id} page 1. Known role-aligned "
            "anchors on BOTH documents: the first two PROJECTS entry-head lines "
            "(verbatim line prefixes `ImageCaptioningSystem` and "
            "`SentimentAnalysisAPI`). Report localized findings with a typed "
            "role-gap measurement request over those anchors."
        )
        if reviewer is not None:
            target_pages = _render_pages(target_pdf, out_dir, f"review_target_{attempt}")
            render_pages = _render_pages(current_pdf, out_dir, f"review_render_{attempt}")
            findings = reviewer(
                [target_pages[0], render_pages[0]],
                finding_id=f"finding-{counter['finding']:03d}",
                target_version=target_id,
                render_version=current_version.version_id,
                page=1,
                node_inventory=json.dumps([n.node_id for n in state.nodes]),
                budget=budget,
                trace=trace,
            )
        else:
            findings = ScriptedReviewer().run(
                prompt,
                finding_id=f"finding-{counter['finding']:03d}",
                target_version=target_id,
                render_version=current_version.version_id,
                page=1,
                budget=budget,
                trace=trace,
            )
        findings = _validate_finding_versions(findings, target_id, current_version.version_id)
        findings_out = list(findings)
        findings.extend(findings_out)
        for finding in findings_out:
            result, attribution = measure_and_attribute(finding, pdf=current_pdf)
            request_id = result.request_id  # the request id to repeat identically
            if attribution.repair_owner != "builder":
                # No confirmed attributed defect for the builder this round:
                # strategy escalation records the observation and moves on
                # (never unsupported, never a silent pass).
                attempted_strategies.append(
                    f"attempt{attempt}:{finding.finding_id}:{attribution.attribution}:remeasure_or_other_channel"
                )
                continue
            # Repair: one bounded layer change, validated by the shell.
            fingerprint = f"{attribution.attribution}:{finding.suspected_dimension}:{round(result.delta_pt or 0, 3)}"
            if fingerprint in fingerprints:
                attempted_strategies.append(
                    f"attempt{attempt}:{finding.finding_id}:repeated_action_change_strategy"
                )
                continue
            fingerprints.append(fingerprint)
            base_gap = _plan_gap_of(out_dir, state)
            proposal = RepairProposal(
                finding_id=finding.finding_id,
                base_render_version=current_version.version_id,
                layer="plan_entry_gap",
                section_node_id=SECTION_NODE_ID,
                gap_delta_pt=-3.0 if result.delta_pt and result.delta_pt > 0 else 3.0,
                rationale="attributed plan-layer role gap; bounded single-layer correction",
                agent="scripted" if reviewer is None else "llm",
            )
            validation_error = _validate_repair(proposal, current_version, fingerprints)
            if validation_error:
                repair_attempts.append(
                    {"finding": finding.finding_id, "rejected": validation_error, "attempt": attempt}
                )
                trace.add(agent="shell", phase="repair", action="rejected", note=validation_error)
                continue
            repair_attempts.append(
                {
                    "finding": finding.finding_id,
                    "attempt": attempt,
                    "layer": proposal.layer,
                    "gap_delta_pt": proposal.gap_delta_pt,
                    "base": proposal.base_render_version,
                }
            )
            trace.add(
                agent="builder",
                phase="repair",
                action="proposal",
                output=proposal.model_dump(mode="json"),
                persist_output=True,
            )
            # Candidate version (never overwrites approved state) and the
            # IDENTICAL measurement request on the new render.
            candidate_version, candidate_pdf, candidate_gates = render_version(
                proposal.gap_delta_pt, f"repair attempt {attempt} for {finding.finding_id}"
            )
            if not candidate_version.hard_gates_passed:
                # Failed repair: rolled back; the best valid version is kept.
                attempted_strategies.append(
                    f"attempt{attempt}:{finding.finding_id}:repair_failed_gates_rolled_back"
                )
                trace.add(agent="shell", phase="repair", action="rolled_back", note="gates failed")
                continue
            repeat_request = finding.requested_measurement.model_copy(
                update={"request_id": request_id}  # IDENTICAL request id
            )
            repeat_result = MeasureController(pod, budget, trace).execute(
                repeat_request, current_pdf=candidate_pdf
            )
            improved = (
                repeat_result.current_value_pt is not None
                and result.current_value_pt is not None
                and repeat_result.target_value_pt is not None
                and abs(repeat_result.current_value_pt - repeat_result.target_value_pt)
                < abs(result.current_value_pt - result.target_value_pt)
            )
            if improved and candidate_version.hard_gates_passed:
                candidate_version = candidate_version.model_copy(update={"promoted": True})
                versions[-1] = candidate_version
                best = {"version_id": candidate_version.version_id, "gates": candidate_gates}
                open_findings = [
                    f.finding_id for f in findings if f.finding_id not in _repaired_ids(repair_attempts)
                ]
                trace.add(
                    agent="shell",
                    phase="repair",
                    action="promoted",
                    output={
                        "version": candidate_version.version_id,
                        "before": result.model_dump(mode="json"),
                        "after": repeat_result.model_dump(mode="json"),
                    },
                    persist_output=True,
                )
            else:
                # Regression / non-improvement: roll back, keep the best valid
                # version, escalate strategy.
                attempted_strategies.append(
                    f"attempt{attempt}:{finding.finding_id}:non_improving_rolled_back"
                )
                trace.add(agent="shell", phase="repair", action="rolled_back", note="non-improving repair")

    # 6. Content-shape probes (short/long/missing-field/nested-entry proxies via
    # the canonical C2 flow probe on independent fixtures; the canonical probe
    # lane remains C2's own content-shape verification, which already ran).
    from tests.experiments.c2_candidates import independent_candidate_fixtures

    shape_probes: dict[str, Any] = {}
    for profile, probe_candidate in independent_candidate_fixtures().items():
        shape_probes[profile] = run_flow_probe(state, probe_candidate)
    (out_dir / "content_shape_probes.json").write_text(
        json.dumps(shape_probes, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    probes_ok = all(entry["passed"] for entry in shape_probes.values())

    # 7. Terminal state (plan §14): two normal exits only. A promoted render
    # followed by a rolled-back attempt leaves `versions[-1]` on the
    # rolled-back candidate — the BEST version is the promoted one, which is
    # what delivery readiness checks.
    best_version_id = next(
        (v.version_id for v in versions if v.promoted),
        next((v.version_id for v in versions if v.hard_gates_passed), None),
    )
    best_version = next((v for v in versions if v.version_id == best_version_id), None)
    all_material_reviewed = bool(findings) and not open_findings
    if best_version is not None and best_version.hard_gates_passed and all_material_reviewed and probes_ok:
        terminal = "delivered_pending_owner"
    else:
        # Includes budget end, stalled escalation, and remaining open findings:
        # never success, always resumable.
        terminal = "budget_exhausted"
    record = E2LoopRecord(
        target_id=target_id,
        render_versions=versions,
        best_render_version=best_version_id,
        findings=findings,
        measurement_results=measurement_results,
        attributions=attributions,
        repair_attempts=repair_attempts,
        attempted_strategies=attempted_strategies,
        action_fingerprints=fingerprints,
        open_findings=[f.finding_id for f in findings if f.finding_id in open_findings]
        if open_findings
        else [f.finding_id for f in findings if f.finding_id not in _repaired_ids(repair_attempts)],
        budget_state=budget.to_json(),
        summary={
            "total_findings": len(findings),
            "terminal_state": terminal,
            "best_render_version": best_version_id,
            "content_shape_probes_passed": probes_ok,
            "coverage_audit_status": coverage.get("status"),
        },
    )
    (out_dir / "e2_state.json").write_text(
        json.dumps(record.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    store.manifest["terminal_state"] = terminal
    store.manifest["e2"] = record.model_dump(mode="json")
    store.manifest["budget"] = budget.to_json()
    store.manifest["pending_candidate_id"] = None  # candidates stay INACTIVE; the owner decides
    # Replace the registration entries with the promoted-state records (a
    # promoted candidate updates the version note; nothing is overwritten on
    # disk — pdf/html artifacts stay immutable and checksummed).
    store.manifest["versions"] = [
        entry
        for entry in store.manifest["versions"]
        if entry["id"] not in {v.version_id for v in versions}
    ]
    store.manifest["versions"].extend(
        {
            "id": version.version_id,
            "path": f"render_{index + 1}.pdf",
            "sha256": version.pdf_sha256,
            "note": version.note,
            "promoted": version.promoted,
            "hard_gates_passed": version.hard_gates_passed,
        }
        for index, version in enumerate(versions)
    )
    store.record_state(terminal, f"best={best_version_id}")
    (out_dir / "manifest.json").write_text(
        json.dumps(store.manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    trace.save()
    _write_e2_report(out_dir, target_frozen, record, terminal)
    return out_dir, terminal, record.model_dump(mode="json")


SECTION_NODE_ID = "section.04"


def _render_pdf_of(out_dir: Path, index: int) -> Path:
    return out_dir / f"render_{index}.pdf"


def current_gates_of(out_dir: Path, index: int) -> dict[str, Any]:
    path = out_dir / f"hard_gates_render-{_stem_of(out_dir)}-v{index}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))["gates"]
    return {}


def _stem_of(out_dir: Path) -> str:
    manifest = out_dir / "manifest.json"
    if manifest.exists():
        data = json.loads(manifest.read_text(encoding="utf-8"))
        return (data.get("e2", {}).get("target_id") or "target").removeprefix("target-").removesuffix("-v1")
    return "x"


def _plan_gap_of(out_dir: Path, state: Any) -> float | None:
    node = next((n for n in state.nodes if n.node_id == SECTION_NODE_ID), None)
    return None if node is None else None


def _validate_finding_versions(
    findings: list[DefectFinding], target_version: str, render_version: str
) -> list[DefectFinding]:
    """An observation without exact artifact versions is invalid (plan §8.1)."""
    valid: list[DefectFinding] = []
    for finding in findings:
        if finding.target_version == target_version and finding.render_version == render_version:
            valid.append(finding)
    return valid


def _repaired_ids(repair_attempts: list[dict[str, Any]]) -> set[str]:
    return {entry["finding"] for entry in repair_attempts if "finding" in entry}


def _validate_repair(
    proposal: RepairProposal, current_version: RenderVersion, fingerprints: list[str]
) -> str | None:
    """Deterministic repair authorization: base version, layer scope, and the
    candidate-facts boundary (plan §8.4)."""
    if proposal.layer == "no_op":
        return "no_op repairs are not authorized"
    if proposal.base_render_version != current_version.version_id:
        return "stale base version"
    if proposal.layer == "plan_entry_gap" and proposal.section_node_id != SECTION_NODE_ID:
        return "repair scope outside the diagnosed node"
    if proposal.style_updates:
        # The Builder may not touch candidate FACTS — styles are presentation.
        forbidden = [key for key in proposal.style_updates if key.startswith("leaf_")]
        if forbidden:
            return f"candidate fact mutation is forbidden: {forbidden}"
    return None


def prompt_region(prompt: str) -> str:
    return "section.04"


def prompt_observation(prompt: str) -> str:
    return (
        "The vertical distance from the first dated entry head to the second "
        "dated entry head appears larger than in the target."
    )


def prompt_request(prompt: str, finding_id: str) -> MeasurementRequest:
    return MeasurementRequest(
        request_id="measure-pending",
        metric="role_gap",
        page=1,
        # Role-aligned anchors per side (target PROJECTS entry heads vs the
        # candidate render's own first two entry heads — different text, same
        # semantic role; plan §9 rule 3).
        from_text="ImageCaptioningSystem",
        to_text="SentimentAnalysisAPI",
        render_from_text="Microsoft",
        render_to_text="Amazon.com",
        region_id="section.04",
    )


def _write_e2_report(
    out_dir: Path,
    frozen: FrozenCase,
    record: E2LoopRecord,
    terminal: str,
) -> None:
    findings_table = "\n".join(
        f"| `{f.finding_id}` | {f.render_version} | {f.region} | {f.observation} |"
        f" {f.requested_measurement.from_text} -> {f.requested_measurement.to_text} |"
        for f in record.findings
    ) or "| - | - | - | - | - |"
    versions_table = "\n".join(
        f"| `{v.version_id}` | {v.hard_gates_passed} | {v.promoted} | {v.note} |"
        for v in record.render_versions
    ) or "| - | - | - | - |"
    attempts_table = "\n".join(
        f"| {strategy} |" for strategy in record.attempted_strategies
    ) or "| - |"
    (out_dir / "REPORT.md").write_text(
        f"""# Pipeline E2 See/Measure/Attribute/Repair run — {out_dir.name}

- Case: `{frozen.case_id}`; target sha256 `{frozen.target_sha256}`
- Terminal state: **{terminal}**
- Best valid render: `{record.best_render_version}`
- Model requests: {record.budget_state.get("model_request_count")}/{record.budget_state.get("max_model_requests")};
  tool calls: {record.budget_state.get("tool_call_count")}/{record.budget_state.get("max_tool_calls")}

## Render versions (each is a whole-document render; candidates stay INACTIVE)

| version | hard gates | promoted | note |
| --- | --- | --- | --- |
{versions_table}

## Findings (observation-first, version-bound)

| finding | render | region | observation | measurement anchors |
| --- | --- | --- | --- | --- |
{findings_table}

## Attempted strategies (incl. escalations)

|
{attempts_table}

## What this run does and does not establish

Establishes: the bounded See -> Measure -> Attribute -> Repair -> Re-render
loop runs offline through the existing C2 components with REAL Chrome renders
and REAL final-PDF pdfplumber measurement; findings bind exact versions;
repairs are validated, re-measured with the identical request, and rolled back
when they fail or regress; the best valid version is preserved; a
budget-exhausted run is resumable and never success.

Does **not** establish: any visual acceptance or fidelity winner. The owner
reviews the final HTML/PDF (T-v1) and decides; automated metrics declare
nothing (plan §13). No live model call was made and no ADR/product contract
changed.

## Authority boundary

Experiment-only under `tests/experiments/` (PIPELINE_E_PLAN.md §5, E_PIPELINE_PREP.md
§5). DOCX stays out; editable HTML + Chrome PDF is the render surface. The
content-shape probe profiles are the canonical C2 independent fixtures through
`run_flow_probe`; the canonical probe lane remains C2's own verification.
""",
        encoding="utf-8",
    )


class DualSourcePod(EvidencePod):
    """E2 evidence pod: the SAME read-only evidence tools as E1, plus
    render-source crops and measurements against ACTUAL final PDF bytes.
    Target evidence stays immutable; the render pdf is output verification."""

    def __init__(
        self,
        target_pdf: Path,
        adobe_json: Any,
        out_dir: Path,
        *,
        render_pdf: Path | None = None,
        normalized: Any | None = None,
    ) -> None:
        if isinstance(adobe_json, (str, Path)):
            super().__init__(target_pdf, Path(adobe_json), out_dir, normalized=normalized)
        else:  # already-parsed raw payload (dict) — store directly
            self.target_pdf = target_pdf.resolve()
            self.adobe_json = Path("adobe_raw.json")
            self.out_dir = out_dir
            self.raw = adobe_json
            self.structured_data = self.raw.get("structured_data") or self.raw
            self.normalized = normalized
            self.page_sizes = _raw_page_sizes(self.structured_data)
            self.page_count = len(self.structured_data.get("pages") or [])
            self.records: list[EvidenceAccessRecord] = []
            self.artifacts: dict[str, Path] = {}
            self._page_images: dict[str, list[Path]] = {}
            self._sequence = 0
        self.render_pdf = render_pdf
        self._render_page_images: list[Path] | None = None

    # -- page pixels for both sources ----------------------------------------

    def page_pixels(self, page_number: int, source: str = "target") -> tuple[list[Path], int, int]:
        pdf = self.render_pdf if source == "render" else self.target_pdf
        if pdf is None:
            raise ValueError("no candidate render bound to this pod")
        key = f"{source}:{pdf.name}"
        if key not in self._page_images:
            prefix = f"{source}_{pdf.stem}"
            self._page_images[key] = _render_pages(pdf, self.out_dir, prefix)
        pages = self._page_images[key]
        if not 1 <= page_number <= len(pages):
            raise ValueError(f"page {page_number} outside 1..{len(pages)}")
        from PIL import Image

        with Image.open(pages[page_number - 1]) as image:
            size = image.size
        return pages, size[0], size[1]

    def inspect_page_region(
        self,
        page_number: int,
        bbox_pt: list[float],
        source: Literal["target", "render"] = "target",
        note: str = "",
    ) -> list[Any]:
        """Original-resolution crop of the TARGET or the RENDER version."""
        if source == "render" and self.render_pdf is None:
            evidence_id = self._next_id("region")
            reason = "no candidate render exists at this checkpoint"
            self._record(
                EvidenceAccessRecord(
                    evidence_id=evidence_id,
                    kind="region_crop",
                    provider="pypdfium2",
                    tool="inspect_page_region",
                    status="unavailable",
                    reason=reason,
                    request={"page": page_number, "bbox_pt": bbox_pt, "source": source},
                )
            )
            return [json.dumps({"evidence_id": evidence_id, "status": "unavailable", "reason": reason}, indent=1)]
        return super().inspect_page_region(page_number, bbox_pt, source, note)

    def measure_render_words(self, page_number: int) -> dict[str, Any]:
        """Page-local word boxes of the CURRENT render pdf (output verification
        channel; never sent to any provider)."""
        import pdfplumber

        evidence_id = self._next_id("measure")
        if self.render_pdf is None:
            self._record(
                EvidenceAccessRecord(
                    evidence_id=evidence_id,
                    kind="local_measurement",
                    provider="pdfplumber",
                    tool="measure_render_words",
                    status="unavailable",
                    reason="no candidate render exists at this checkpoint",
                )
            )
            return {"evidence_id": evidence_id, "status": "unavailable"}
        rows: list[dict[str, Any]] = []
        try:
            with pdfplumber.open(self.render_pdf) as document:
                page = document.pages[page_number - 1]
                for word in page.extract_words():
                    rows.append(
                        {
                            "text": word["text"],
                            "x0": round(float(word["x0"]), 3),
                            "top": round(float(word["top"]), 3),
                            "x1": round(float(word["x1"]), 3),
                            "bottom": round(float(word["bottom"]), 3),
                        }
                    )
        except Exception as error:
            self._record(
                EvidenceAccessRecord(
                    evidence_id=evidence_id,
                    kind="local_measurement",
                    provider="pdfplumber",
                    tool="measure_render_words",
                    status="unavailable",
                    reason=f"render word measurement failed: {error}",
                )
            )
            return {"evidence_id": evidence_id, "status": "unavailable", "reason": str(error)}
        artifact = self.out_dir / f"measurement_{evidence_id}.json"
        artifact.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
        self._record(
            EvidenceAccessRecord(
                evidence_id=evidence_id,
                kind="local_measurement",
                provider="pdfplumber",
                tool="measure_render_words",
                status="available",
                artifact=artifact.name,
                source_path=self.render_pdf.name,
                request={"page": page_number},
            )
        )
        return {
            "evidence_id": evidence_id,
            "status": "available",
            "source_kind": "render",
            "source_path": self.render_pdf.name,
            "source_sha256": _sha256_file(self.render_pdf),
            "page": page_number,
            "rows": rows,
        }

    def pdf_line_rows(self, pdf: Path, page_number: int) -> list[dict[str, Any]]:
        """Page-local visual LINES of one final PDF (the measurement object:
        joined text per baseline row with raw word boxes retained)."""
        import pdfplumber

        rows: list[dict[str, Any]] = []
        with pdfplumber.open(pdf) as document:
            page = document.pages[page_number - 1]
            grouped: dict[float, list[dict[str, Any]]] = {}
            for word in page.extract_words():
                top = round(float(word["top"]), 1)
                grouped.setdefault(top, []).append(word)
            for top in sorted(grouped):
                words = sorted(grouped[top], key=lambda w: w["x0"])
                rows.append(
                    {
                        "page": page_number,
                        "top": top,
                        "text": " ".join(w["text"] for w in words),
                        "words": [
                            {"text": w["text"], "x0": round(float(w["x0"]), 3), "x1": round(float(w["x1"]), 3)}
                            for w in words
                        ],
                    }
                )
        return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pipeline E0/E1 evidence experiment")
    parser.add_argument("--target", type=Path, required=True, help="target PDF")
    parser.add_argument(
        "--adobe-json",
        type=Path,
        default=None,
        help="cached Adobe response (E1); E2 reads the target cache itself",
    )
    parser.add_argument("--normalized", type=Path, default=None, help="normalized evidence (coverage audit only)")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--case-id", default=None)
    parser.add_argument("--case-role", default="diagnostic_known", choices=["diagnostic_known", "frozen_blind"])
    parser.add_argument("--live", action="store_true", help="use a live model (requires authorization)")
    parser.add_argument("--e2", action="store_true", help="run the E2 See/Measure/Attribute/Repair loop (plan §8)")
    parser.add_argument("--decide", type=Path, default=None)
    parser.add_argument("--decision", choices=["accept", "reject"], default=None)
    args = parser.parse_args(argv)

    if args.decide:
        if not args.decision:
            parser.error("--decide requires --decision accept|reject")
        print(owner_decide(args.decide, args.decision))
        return 0

    if args.e2:
        run_dir, terminal, record = run_e2(
            args.target,
            args.out,
            live=args.live,
        )
        print(f"E2 run {run_dir.name}: terminal state {terminal}")
        print(
            f"  findings={record['summary']['total_findings']} "
            f"best={record['best_render_version']} budget={record['budget_state']}"
        )
        print(
            "The final render is owner-reviewable; automated metrics declare nothing. "
            "A budget-exhausted run is resumable, never success."
        )
        return 0

    if not args.adobe_json:
        parser.error("--adobe-json is required for the E1 target-understanding run")

    run_dir, terminal, draft = run_e1(
        args.target,
        args.adobe_json,
        args.out,
        live=args.live,
        case_role=args.case_role,
        case_id=args.case_id,
        normalized_path=args.normalized,
    )
    print(f"E1 run {run_dir.name}: terminal state {terminal}")
    print(
        f"  claims={len(draft.structure)} unresolved={len(draft.unresolved)} "
        f"self_reported={draft.self_reported.status}"
    )
    print("This run is understanding-only; no template is produced or promoted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
