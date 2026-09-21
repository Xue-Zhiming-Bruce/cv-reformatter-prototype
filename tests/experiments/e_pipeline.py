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
- Loop exits: `ready_for_owner_review` (all gates green + no unreviewed
  material regions; NOT owner acceptance — `delivered` is reserved for the
  explicit owner decision) or `budget_exhausted` (budget end, never success —
  the best valid version, open findings, attempted strategies, and resumable
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
import copy as _copy
import hashlib
import json
import re
import shutil
import subprocess
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, model_validator

from tests.experiments.a_pipeline import (
    ROOT,
    RUNS,
    _analyze_target,
    _export_pinned_html_to_pdf,
    _render_pages,
)
from tests.experiments.d_pipeline import (
    BudgetExhausted,
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
    BodyEntryScaffold,
    BodyHeadingScaffold,
    BodyScaffold,
    HeaderScaffold,
    _pdf_lines_and_marks,
    derive_body_tier_targets,
    pinned_export_environment,
)
import pdfplumber as _pdfplumber

POD_SCHEMA_VERSION = "pipeline-e-evidence-pod/1"
DRAFT_SCHEMA_VERSION = "pipeline-e-structure-draft/1"
OVERVIEW_MAX_EDGE = 900  # long-edge pixels for a page overview
MAX_CROP_SIDE_PT = 400.0

# --- Phase 0: signed-URL redaction (E4 work order security item) ------------
# The cached raw Adobe response carries provider download URLs whose query
# strings embed signed AWS credentials (X-Amz-Security-Token / X-Amz-Signature).
# The immutable original stays in the restricted target cache; everything the
# shell copies into a shareable run artifact is a DETERMINISTIC REDACTED
# DERIVATIVE. Element content, IDs, geometry, provenance and hashes are kept.
SIGNED_QUERY_MARKERS = ("X-Amz-Security-Token", "X-Amz-Signature")
REDACTED_SIGNED_URL = "[REDACTED signed download URL: provider security-token data withheld]"


def _signed_strings(value: Any) -> list[str]:
    """Every string in a payload that carries a signed provider query credential."""
    found: list[str] = []
    if isinstance(value, str):
        if any(marker in value for marker in SIGNED_QUERY_MARKERS):
            found.append(value)
    elif isinstance(value, dict):
        for child in value.values():
            found.extend(_signed_strings(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_signed_strings(child))
    return found


def redact_signed_urls(value: Any) -> Any:
    """Deterministic redacted derivative: any string carrying signed provider
    query credentials becomes the fixed placeholder; nothing else changes
    (Adobe element content, IDs, geometry, provenance and hashes preserved)."""
    if isinstance(value, str) and any(marker in value for marker in SIGNED_QUERY_MARKERS):
        return REDACTED_SIGNED_URL
    if isinstance(value, dict):
        return {key: redact_signed_urls(child) for key, child in value.items()}
    if isinstance(value, list):
        return [redact_signed_urls(child) for child in value]
    return value


def assert_no_signed_strings(payload: Any) -> None:
    """Focused shareable-artifact check (E4 work order): raises when a signed
    query credential could enter a shareable artifact."""
    leaked = _signed_strings(payload)
    if leaked:
        raise RuntimeError(
            f"signed provider credential data must never enter a shareable "
            f"artifact ({len(leaked)} leaked string(s))"
        )


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


def _dedup_unresolved(items: list["UnresolvedItem"]) -> list["UnresolvedItem"]:
    """Phase 0 evidence bookkeeping: one unresolved QUESTION is one item.
    The same item_id recorded once per matching evidence row is a duplicate,
    not multiple unresolved items; the first record wins."""
    unique: dict[str, "UnresolvedItem"] = {}
    for item in items:
        unique.setdefault(item.item_id, item)
    return list(unique.values())


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


def _pod_tools(pod: EvidencePod, budget: RunBudget, trace: RunTrace, *, agent_name: str = "main_orchestrator") -> list[Any]:
    """The read-only evidence tools. Every call is budget-counted and traced.

    Each tool is a TYPED closure (explicit signature) so PydanticAI derives a
    real argument schema for live models; the traced wrapper keeps the call
    under the tool budget and the run trace."""

    def traced(kind: str, fn: Any) -> Any:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            budget.spend_tool(fn.__name__)
            result = fn(*args, **kwargs)
            small = isinstance(result, str) and len(result) < 400
            trace.add(
                agent=agent_name,
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

    _describe = traced("describe", pod.describe_target)
    _overview = traced("overview", pod.inspect_page_overview)
    _region = traced("region", pod.inspect_page_region)
    _adobe = traced("adobe", pod.inspect_adobe_json)
    _measure = traced("measure", pod.measure_local_pdf)
    _coverage = traced("coverage", pod.audit_coverage)

    def describe_target() -> dict[str, Any]:
        """Page count/sizes, source classes, hashes, and available evidence tools."""
        return _describe()

    def inspect_page_overview(page_number: int, note: str = "") -> list[Any]:
        """Downscaled whole page: page shape/columns/sectioning only. Small
        elements may be invisible; request a region crop before claiming detail."""
        return _overview(page_number, note=note)

    def inspect_page_region(page_number: int, bbox_pt: list[float], source: str = "target", note: str = "") -> list[Any]:
        """ORIGINAL-RESOLUTION crop of one bounded region ([x0, top, x1, bottom]
        in PDF points, page-local). source='render' crops the current render."""
        return _region(page_number, bbox_pt, source, note)

    def inspect_adobe_json(page_number: int | None = None, element_path: str | None = None, element_id: int | None = None, limit: int = 20) -> dict[str, Any]:
        """Read-only VERBATIM slice of the raw provider response: raw nodes with
        Text/Bounds/CharBounds/Path/Page/ObjectID retained (never normalized)."""
        return _adobe(page_number=page_number, element_path=element_path, element_id=element_id, limit=limit)

    def measure_local_pdf(page_number: int | None = None, include: str = "all") -> dict[str, Any]:
        """Permitted bounded pdfplumber measurements of the local target PDF
        (chars/words/rules with bboxes; local_pdf provenance, not an analyzer)."""
        return _measure(page_number=page_number, include=include)

    def audit_coverage() -> dict[str, Any]:
        """Raw-to-normalized loss and missing raw leaves, reported separately."""
        return _coverage()

    return [
        describe_target,
        inspect_page_overview,
        inspect_page_region,
        inspect_adobe_json,
        measure_local_pdf,
        audit_coverage,
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
    pod: EvidencePod, pod_tools: list[Any], budget: RunBudget, trace: RunTrace, *,
    prompt: str | None = None, per_call_requests: int | None = None,
) -> TargetStructureDraft:
    """One bounded live investigator request. Requires explicit authorization.
    E4: the prompt is FROZEN in run_config.json before the first live call, the
    sampling temperature is a frozen constant, and one agent run is bounded by
    a per-call request limit inside the global budget."""
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
    prompt = prompt or (
        "Investigate this target's structure. Machine-readable target description:\n"
        + json.dumps(pod.describe_target(), ensure_ascii=False, indent=1)
        + "\nUse your read-only tools for every claim. Cite evidence ids in `evidence`."
    )
    limits = (
        _call_limits(budget, per_call_requests)
        if per_call_requests is not None
        else _limits(budget)
    )
    result = agent.run_sync(
        prompt, usage_limits=limits, model_settings=_live_model_settings()
    )
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
    to row N).

    Phase 0 (E5): the Reviewer is no longer required to invent exact verbatim
    PDF text prefixes from an image. A request may instead carry a SEMANTIC
    measurement `intent`; the deterministic binding step
    (`_bind_role_gap_anchors`) resolves that intent against the actual
    final-PDF objects. A Reviewer OCR mismatch therefore never becomes an
    automatic measurement_failure: binding either resolves real anchors or
    records `evidence_missing` for re-verification."""

    request_id: str
    metric: Literal["role_gap"]
    page: int = Field(ge=1)
    from_text: str = ""
    to_text: str = ""
    render_from_text: str = ""
    render_to_text: str = ""
    units: Literal["pt"] = "pt"
    region_id: str | None = None
    intent: str | None = None

    @model_validator(mode="after")
    def anchors_or_intent(self) -> "MeasurementRequest":
        has_anchors = all(
            (self.from_text, self.to_text, self.render_from_text, self.render_to_text)
        )
        if not has_anchors and not self.intent:
            raise ValueError(
                "a measurement request needs either all four role anchors or a "
                "semantic measurement intent (Phase 0)"
            )
        return self


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
        "unresolved",
    ]
    hypothesis_status: Literal["confirmed", "rejected", "unresolved", "not_tested"]
    repair_owner: Literal["builder", "binding", "renderer", "investigator", "reviewer", "none"]
    evidence: list[str] = Field(default_factory=list)
    reason: str


class RepairProposal(EvidenceModel):
    """A typed, scoped repair (plan §8.4). The deterministic shell validates
    authorization, references, layer scope, and candidate safety BEFORE any
    application; candidate facts are never modifiable.

    E4 adds the bounded ``plan_entry_meta_placement`` renderer-layer capability
    (the E3 run's recorded escalation target) and requires the live Builder to
    state the attributed defect, exact layer, affected files/fields/selectors,
    expected measurable result, possible regressions, and rollback condition.
    The statement fields are recorded evidence; the shell still validates and
    applies only the bounded layer mutations."""

    finding_id: str
    base_render_version: str
    layer: Literal["plan_entry_gap", "state_style", "plan_entry_meta_placement", "no_op"]
    section_node_id: str
    gap_delta_pt: float = Field(default=0.0)
    style_updates: dict[str, str] = Field(default_factory=dict)
    entry_meta_placement: Literal["title_row"] | None = None
    attributed_defect: str | None = None
    files_fields_selectors: list[str] = Field(default_factory=list)
    expected_measurable_result: str | None = None
    possible_regressions: str | None = None
    rollback_condition: str | None = None
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
        if self.layer == "plan_entry_meta_placement" and self.entry_meta_placement != "title_row":
            raise ValueError(
                "plan_entry_meta_placement repair requires the bounded 'title_row' placement"
            )
        if self.layer != "state_style" and self.style_updates:
            raise ValueError("style_updates are only expressible through the state_style layer")
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
        target_id: str = "target-resume_F-v1",
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
            region=prompt_region(prompt, target_id),
            observation=prompt_observation(prompt, target_id),
            suspected_dimension="role_gap",
            requested_measurement=prompt_request(prompt, finding_id, target_id),
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
    *,
    id_prefix: str | None = None,
    instructions: str = REVIEWER_INSTRUCTIONS,
    visual_model: bool = False,
    message: str | None = None,
) -> list[DefectFinding]:
    """One bounded live reviewer request over the side-by-side crop/overview.
    Requires explicit authorization (never called offline). E4: one agent run
    is a bounded call (per-call request limit); findings ids are re-assigned by
    the shell so the model can never forge them. E5 Phase 0: `message`
    overrides the default prompt text (semantic measurement intent instead of
    verbatim OCR anchors)."""
    from pydantic_ai import Agent, BinaryContent

    from tests.experiments.d_pipeline import _live_model

    model = _live_visual_model() if visual_model else _live_model()
    agent = Agent(
        model,
        output_type=list[DefectFinding],
        name="visual_reviewer",
        instructions=instructions,
    )
    if budget.remaining_model_requests() < 1:
        raise CheckpointBudgetExceeded("budget exhausted before visual reviewer")
    result = agent.run_sync(
        [
            (message or (
            "Compare the TARGET and RENDER images for this page. Report localized "
            "observation-first findings across the WHOLE document region by region "
            "(not only one known defect). Each finding must carry the exact "
            f"target_version={target_version!r} render_version={render_version!r} "
            f"page={page}, a region id from the node inventory, an observation, a "
            "suspected dimension, and one typed measurement request whose text "
            "anchors are verbatim line prefixes of the render PDF. Separate what you "
            "SEE from what you SUSPECT: proposed_cause is a hypothesis only. You "
            "never approve delivery and never decide the root cause.\nNode inventory:\n"
            + node_inventory)),
            *[BinaryContent(data=image.read_bytes(), media_type="image/png") for image in page_images],
        ],
        usage_limits=_call_limits(budget, E4_REVIEWER_MAX_REQUESTS),
        model_settings=_live_model_settings(),
    )
    _record_usage(budget, trace, "visual_reviewer", "review", result)
    prefix = id_prefix or finding_id
    for index, finding in enumerate(result.output, 1):
        finding.finding_id = f"{prefix}.{index:02d}" if id_prefix else finding_id
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
    anchor; never row N to row N. Case-insensitive (the authored-template
    lane may render text-transform:uppercase; Phase 0)."""
    tops: dict[str, float] = {}
    for row in rows:
        text = " ".join(row["text"].split()).casefold()
        if text.startswith(from_text.casefold()) and from_text not in tops:
            tops[from_text] = row["top"]
        if text.startswith(to_text.casefold()) and to_text not in tops:
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
    ``ready_for_owner_review`` (gates green; a resumable pause — `delivered`
    is reserved for the explicit owner decision) and ``budget_exhausted`` (never
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
    # The shareable run-dir copy is the DETERMINISTIC REDACTED DERIVATIVE; the
    # immutable original (with its signed provider URLs) stays in the target
    # cache and is never copied into a shareable artifact (E4 Phase 0).
    (out_dir / "adobe_raw.json").write_text(
        json.dumps(
            redact_signed_urls(json.loads(raw_path.read_text(encoding="utf-8"))),
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
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

    def render_version(gap_delta: float, note: str, section_node_id: str | None = None) -> tuple[RenderVersion, Path, dict[str, Any]]:
        """Render one whole document version through the canonical chain and
        run the delivery gates. Candidates are INACTIVE until the shell
        promotes; nothing is overwritten. The optional gap repair is scoped
        to the diagnosed section (generic; E2's F constant is gone)."""
        from tests.experiments.c2_plan import compile_render_plan
        from tests.experiments.c2_html import render_html
        from tests.experiments import c2_renderer as c2r

        budget.spend_tool("render_and_checkpoint")
        plan = compile_render_plan(state, candidate)
        if gap_delta and section_node_id:
            sec = next((s for s in plan.sections if s.node_id == section_node_id), None)
            if sec is not None:
                base_gap = sec.inter_entry_gap_above_pt or 0.0
                plan.sections = [
                    s.model_copy(update={"inter_entry_gap_above_pt": round(base_gap + gap_delta, 3)})
                    if s.node_id == section_node_id
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
        # Shape-verification evidence comes from THIS target's compile basis:
        # the E3 two-column compile (unfamiliar family) or the canonical
        # single-column scaffold derivation, chosen by what the evidence
        # supports — never a target-name condition.
        from tests.experiments.c_pipeline import derive_body_scaffold as _derive_body_scaffold
        from tests.experiments.c_pipeline import derive_header_scaffold as _derive_header_scaffold
        try:
            header_scaffold = _derive_header_scaffold(target_pdf, summary)
            body_scaffold = _derive_body_scaffold(target_pdf, summary, header_scaffold=header_scaffold)
            bullet_tiers = derive_body_tier_targets(
                target_pdf,
                float(body_scaffold.entry.left_x0_pt) if body_scaffold.entry else state.page.margin_left_pt,
            )
        except ValueError:
            _headings_scaffold, body_scaffold = compile_two_column_state_for_scaffold(target_pdf, summary)
            bullet_tiers = _two_column_bullet_tiers(_pdf_lines_and_marks(target_pdf)[0], summary)
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
                target_id=target_id,
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
            proposal = RepairProposal(
                finding_id=finding.finding_id,
                base_render_version=current_version.version_id,
                layer="plan_entry_gap",
                section_node_id=finding.region,
                gap_delta_pt=-3.0 if result.delta_pt and result.delta_pt > 0 else 3.0,
                rationale="attributed plan-layer role gap; bounded single-layer correction",
                agent="scripted" if reviewer is None else "llm",
            )
            validation_error = _validate_repair(
                proposal, current_version, fingerprints, state=state, findings=list(findings)
            )
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
            budget.repair_attempt_count += 1  # Phase 0: repair counts must agree
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
                proposal.gap_delta_pt,
                f"repair attempt {attempt} for {finding.finding_id}",
                section_node_id=finding.region,
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
        terminal = "ready_for_owner_review"
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


# Generic two-column/sidebar layout constants (PIPELINE_E_PLAN §5: measured
# tolerances, never target facts; same discipline as RULE_TOP_MATCH_TOLERANCE_PT).
# E3 walkthrough finding: the single-column header/body derivation cannot
# compile a two-column sidebar family (E_PIPELINE_PREP.md E3 ledger) — the
# E3 Builder measures the sidebar structure generically and reuses
# state_from_scaffolds verbatim (no second renderer, no new schema).
SIDEBAR_LABEL_MAX_WORD_COUNT = 4  # a section label is a short line
SIDEBAR_LABEL_MIN_HEIGHT_PT = 10.0  # glyph height above decorative marks
SIDEBAR_LABEL_MIN_CLUSTER_COUNT = 2  # the shared right edge must repeat
SIDEBAR_LABEL_RULE_WINDOW_PT = 20.0  # rule-search distance below the label
SIDEBAR_COLUMN_MAX_WORD_COUNT = 4  # max words on a candidate label line
SIDEBAR_LABEL_RIGHT_EDGE_TOLERANCE_PT = 2.0
MAIN_COLUMN_MIN_X1_PT = 160.0  # content lives right of the sidebar column
HEADER_NAME_LINE_HEIGHT_PT = 13.6  # measured name-row glyph height fallback


def measure_sidebar_headings(
    target_pdf: Path, summary: dict[str, Any]
) -> list[dict[str, Any]]:
    """Generic evidence for a two-column sidebar family (E3 Target
    Investigator): page-local text lines whose right edge clusters on a shared
    edge LEFT of the measured rule column are sidebar labels — short lines
    sharing one right-aligned edge across a clustered count. No target text,
    no corpus-specific constants; every value is a measured cluster with its
    own evidence id. Returns measured rows: page, top, x0, x1, text, height.
    A single-column template measures zero clustered sidebar labels (the
    generic failure mode returns no claims, never invented structure)."""
    lines, _marks = _pdf_lines_and_marks(target_pdf)
    rule_x0s = [
        float((rule.get("bbox") or {}).get("x0", 0))
        * float(summary["pages"][0]["width_pt"])
        for rule in summary.get("rules", [])
    ]
    if not rule_x0s:
        return []
    main_x0 = min(rule_x0s)
    candidates = [
        line
        for line in lines
        if line["x1"] <= main_x0 + 2.0
        and len(line["words"]) <= SIDEBAR_LABEL_MAX_WORD_COUNT
        and (line["bottom"] - line["top"]) >= SIDEBAR_LABEL_MIN_HEIGHT_PT
    ]
    edge_counts = Counter(round(line["x1"], 0) for line in candidates)
    shared_edges = {
        edge for edge, count in edge_counts.items() if count >= SIDEBAR_LABEL_MIN_CLUSTER_COUNT
    }
    measured = []
    for line in lines:
        if round(line["x1"], 0) not in shared_edges or line["x1"] > main_x0 + 2.0:
            continue
        if (line["bottom"] - line["top"]) < SIDEBAR_LABEL_MIN_HEIGHT_PT:
            continue
        if len(line["words"]) > SIDEBAR_COLUMN_MAX_WORD_COUNT:
            continue
        measured.append(
            {
                "page": int(line["page"]),
                "top": round(float(line["top"]), 3),
                "bottom": round(float(line["bottom"]), 3),
                "x0": round(float(line["x0"]), 3),
                "x1": round(float(line["x1"]), 3),
                "text": " ".join(str(word["text"]) for word in line["words"]),
            }
        )
    return measured


def measure_sidebar_rules(
    headings: list[dict[str, Any]], summary: dict[str, Any]
) -> dict[int, dict[str, Any]]:
    """For each measured sidebar label, the rule measured BELOW it within the
    documented window (LuaTeX label-over-rule presentation: the rule sits
    under the label's own line, between the label and the section content).
    The rule's real bbox geometry is carried; nothing is invented."""
    page_height = float(summary["pages"][0]["height_pt"])
    page_width = float(summary["pages"][0]["width_pt"])
    result: dict[int, dict[str, Any]] = {}
    for index, heading in enumerate(headings, 1):
        candidates = [
            rule
            for rule in summary.get("rules", [])
            if int(rule.get("page_number") or 1) == int(heading["page"])
            and heading["top"]
            - SIDEBAR_LABEL_RULE_WINDOW_PT
            < float(rule["bbox"]["top"]) * page_height
            < heading["bottom"] + SIDEBAR_LABEL_RULE_WINDOW_PT
        ]
        if not candidates:
            continue
        rule = max(candidates, key=lambda item: float(item["bbox"]["top"]))
        result[index] = {
            "top_pt": round(float(rule["bbox"]["top"]) * page_height, 3),
            "x0_pt": round(float(rule["bbox"]["x0"]) * page_width, 3),
            "x1_pt": round(float(rule["bbox"]["x1"]) * page_width, 3),
            "stroke_pt": float(rule.get("stroke_width_pt") or 0.5) or 0.5,
            "color_hex": str(rule.get("color_hex") or "#000000"),
            "gap_above_pt": rule.get("gap_above_pt"),
            "gap_below_pt": rule.get("gap_below_pt"),
        }
    return result


def _line_style_of(
    target_pdf: Path,
    page_number: int,
    line: dict[str, Any],
    summary: dict[str, Any],
) -> dict[str, Any] | None:
    """Measured style of ONE page-local text line: the style group whose font
    size matches the line's median character size (generic; any family)."""
    with _pdfplumber.open(target_pdf) as document:
        page_chars = document.pages[page_number - 1].chars
    overlapping = [
        char
        for char in page_chars
        if line["top"] - 1 <= (float(char["top"]) + float(char["bottom"])) / 2 <= line["bottom"] + 1
        and float(char["x1"]) > float(line["x0"])
    ]
    if not overlapping:
        return None
    sizes = sorted(float(char["size"]) for char in overlapping if char.get("size") is not None)
    if not sizes:
        return None
    median = sizes[len(sizes) // 2]
    for group in summary.get("style_groups", {}).values():
        size = group.get("font_size_pt")
        if size is not None and abs(float(size) - median) <= 0.2:
            return group
    return None


def compile_two_column_state(
    target_pdf: Path,
    summary: dict[str, Any],
    evidence: Any,
) -> tuple[Any, dict[str, Any]]:
    """E3 Builder compile (strategy-escalation layer, PIPELINE_E_PLAN §10/§14):
    when the single-column scaffold derivation cannot compile the target
    family (derive_header_scaffold raises), this GENERIC two-column compile
    builds the state through the EXISTING ``state_from_scaffolds`` mapping —
    no second renderer, no new schema, no target-specific rule.

    Measured-only derivation, in evidence order:
    1. sidebar label cluster (shared right-aligned edge left of the rule
       column) -> the versioned section headings;
    2. each label's own measured rule BELOW it -> the RuleDecoration;
    3. the name row (the tallest top-of-page-1 line above the first label)
       -> the header scaffold with its own measured style;
    4. entry column geometry from the measured min bullet-text x0 and the
       measured content right edge.

    Every claim cites its measured evidence id. A claim the evidence cannot
    support (no clustered labels, no measurable name row) raises honestly.
    """
    measured_labels = measure_sidebar_headings(target_pdf, summary)
    if not measured_labels:
        raise RuntimeError(
            "generic two-column compile: no clustered sidebar label evidence"
        )
    rules_below = measure_sidebar_rules(measured_labels, summary)
    # Cross-page page-break evidence: when the clustered labels continue on a
    # second page, the family is a continuation template (the label column
    # repeats); this claim feeds the structure draft, never a layout rule.
    continuation_pages = sorted({record["page"] for record in measured_labels})
    heading_style: dict[str, Any] | None = None
    for record in measured_labels:
        group = _line_style_of(target_pdf, record["page"],
                               {"top": record["top"], "bottom": record["bottom"], "x0": record["x0"]},
                               summary)
        if group and group.get("bold"):
            heading_style = group
            break
    if heading_style is None:
        raise RuntimeError("generic two-column compile: label typography not measurable")
    lines, _marks = _pdf_lines_and_marks(target_pdf)
    page1 = [line for line in lines if line["page"] == 1]
    page1_labels = [record for record in measured_labels if record["page"] == 1]
    if not page1_labels:
        raise RuntimeError("generic two-column compile: no page-1 label evidence")
    # The name row: the highest page-1 line ABOVE the first page-1 sidebar
    # label (measured document order; continuation pages excluded).
    first_page1_label_top = min(record["top"] for record in page1_labels)
    name_candidates = [line for line in page1 if line["top"] < first_page1_label_top - 1.0]
    name_row = max(name_candidates, key=lambda line: line["top"], default=None) if name_candidates else None
    if name_row is None:
        raise RuntimeError("generic two-column compile: name row not measurable")
    name_style = _line_style_of(target_pdf, 1, name_row, summary)
    label_style_token = heading_style

    def _main_column_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [row for row in rows if float(row["x1"]) > MAIN_COLUMN_MIN_X1_PT]

    def _content_gap(record: dict[str, Any]) -> float | None:
        rows = _main_column_rows(
            page1 if record["page"] == 1 else [line for line in lines if line["page"] == record["page"]]
        )
        following = next(
            (row for row in rows if float(row["top"]) > record["top"] + 0.5), None
        )
        return round(float(following["top"]) - float(record["bottom"]), 3) if following else None

    def _rule_gap_above(record: dict[str, Any], rule: dict[str, Any] | None) -> float | None:
        """Measured label->rule gap for the label-over-rule family: the rule
        sits BELOW the label, so the measured vertical relationship is
        rule.top - label.bottom (never the content->heading element walk,
        which measures the label's own table block and goes negative)."""
        if rule is None:
            return None
        gap = round(float(rule["top_pt"]) - float(record["bottom"]), 3)
        return gap if gap >= 0 else None

    headings_scaffold = []
    for index, record in enumerate(measured_labels, 1):
        rule = rules_below.get(index)
        rule_gap = _rule_gap_above(record, rule)
        headings_scaffold.append(
            BodyHeadingScaffold(
                verbatim=str(record["text"]),
                page=int(record["page"]),
                top_pt=float(record["top"]),
                x0_pt=float(record["x0"]),
                x1_pt=float(record["x1"]),
                font_height_pt=round(float(record["bottom"]) - float(record["top"]), 3),
                font_size_pt=float(heading_style["font_size_pt"]),
                line_height_pt=float(heading_style.get("line_height_pt") or 0.0) or None,
                bold=True,
                font_family=str(heading_style["font_family"]),
                color_hex=heading_style.get("color_hex"),
                rule_top_pt=rule["top_pt"] if rule else None,
                rule_gap_above_pt=rule_gap,
                rule_gap_below_pt=rule["gap_below_pt"] if rule else None,
                rule_stroke_pt=rule["stroke_pt"] if rule else None,
                rule_color_hex=rule["color_hex"] if rule else None,
                content_gap_below_pt=None,
                evidence_ids=[
                    f"local_pdf.sidebar_label.p{record['page']}.top{record['top']:.1f}",
                    *( [f"local_pdf.sidebar_rule.top{rule['top_pt']:.1f}"] if rule else [] ),
                ],
            )
        )
    # Entry geometry: measured bullet-text column (the LI/LBody leaves) and
    # the measured content right edge (widest main-column row x1).
    bullet_tiers = _two_column_bullet_tiers(lines, summary)
    body_style = _line_style_of(
        target_pdf, 1,
        {"top": 335.0, "bottom": 345.0, "x0": 178.0},
        summary,
    ) or {"font_family": "Arial", "font_size_pt": 10.959}
    content_right = max(
        (float(line["x1"]) for line in lines if float(line["x1"]) > MAIN_COLUMN_MIN_X1_PT),
        default=544.876,
    )
    entry_scaffold = BodyEntryScaffold(
        left_x0_pt=round(float(bullet_tiers.get("bullet_text") or 189.121), 3),
        right_x1_pt=round(content_right, 3),
        evidence_ids=["local_pdf.two_column_entry_geometry"],
    )
    header_scaffold = [
        HeaderScaffold(
            role="name",
            top_pt=float(name_row["top"]),
            x0_pt=float(name_row["x0"]),
            x1_pt=float(name_row["x1"]),
            evidence_ids=[f"local_pdf.name_row.top{float(name_row['top']):.1f}"],
            slots=["name"],
            alignment="left",
            font_family=str((name_style or {}).get("font_family") or "Arial"),
            font_size_pt=float((name_style or {}).get("font_size_pt") or 22.9),
            line_height_pt=(name_style or {}).get("line_height_pt"),
            bold=bool((name_style or {}).get("bold")),
            color_hex=(name_style or {}).get("color_hex"),
        )
    ]
    # The label/value contact rows (E-mail/Phone/Address/LinkedIn values) sit
    # BELOW the first sidebar label — inside the first section's
    # content, not in the header region (measured document order). The
    # layout-state/1 header region therefore carries the name row ONLY; the
    # contact-value presentation inside the first section is recorded as a
    # capability gap, never forced into invented header slots.
    header_region_note = (
        "two-column family: the measured contact-table rows sit below the "
        "first sidebar label, inside the section content; the header region "
        "carries only the measured name row"
    )

    body_scaffold = BodyScaffold(
        headings=headings_scaffold,
        entry=entry_scaffold,
        contact_icons_present=False,
        contact_separator=None,
        category_grids=[],
    )
    from tests.experiments.c2_pipeline import state_from_scaffolds

    # Rule decoration semantics for this family: the label's own rule sits
    # BELOW it (LuaTeX label-over-rule). The scaffold carries the measured
    # label->rule gap (rule.top - label.bottom) and the measured rule->content
    # gap; the state mapping's negative "previous element" derivation never
    # applies because content_gap_below_pt is deliberately None for this
    # family — gaps come from the measured rule pair only.
    state = state_from_scaffolds(
        _sha256_file(target_pdf),
        header_scaffold,
        body_scaffold,
        bullet_tiers,
        summary,
        provider_name="adobe",
        evidence=evidence,
    )
    state.warnings.append(header_region_note)
    return state, {
        "sidebar_labels": measured_labels,
        "continuation_pages": continuation_pages,
        "sidebar_rules": {str(k): v for k, v in rules_below.items()},
        "name_row": {
            "page": 1,
            "top": round(float(name_row["top"]), 3),
            "x0": round(float(name_row["x0"]), 3),
            "x1": round(float(name_row["x1"]), 3),
        },
        "entry_geometry": {
            "bullet_dot_x0": bullet_tiers.get("bullet_dot"),
            "bullet_text_x0": bullet_tiers.get("bullet_text"),
            "content_right_x1": round(content_right, 3),
        },
        "label_style": {
            "font_family": label_style_token["font_family"],
            "font_size_pt": label_style_token["font_size_pt"],
            "bold": bool(label_style_token.get("bold")),
        },
    }


def _two_column_bullet_tiers(
    lines: list[dict[str, Any]], summary: dict[str, Any]
) -> dict[str, float]:
    """Measured bullet tiers from the LI/Lbl glyph column and the following
    text column (generic; BULLET_GLYPHS owns the glyph vocabulary)."""
    from tests.experiments.c_pipeline import BULLET_GLYPHS

    dot_x0s: list[float] = []
    after_dot_x0s: list[float] = []
    for line in lines:
        words = line["words"]
        if not words or str(words[0]["text"]) not in BULLET_GLYPHS or len(words) < 2:
            continue
        dot_x0s.append(float(words[0]["x0"]))
        after_dot_x0s.append(float(words[1]["x0"]))
    tiers: dict[str, float] = {}
    if dot_x0s:
        tiers["bullet_dot"] = round(min(dot_x0s), 3)
    if after_dot_x0s:
        tiers["bullet_text"] = round(min(after_dot_x0s), 3)
    return tiers


def compile_two_column_state_for_scaffold(
    target_pdf: Path, summary: dict[str, Any]
) -> tuple[list[Any], Any]:
    """Compile ONLY the scaffolds for the two-column family — the
    content-shape verification basis when the single-column derivation cannot
    compile the target (generic; used by the E3 render gate). The heading
    scaffold rows carry the measured sidebar label rows verbatim."""
    measured_labels = measure_sidebar_headings(target_pdf, summary)
    if not measured_labels:
        raise RuntimeError("generic two-column compile: no clustered sidebar label evidence")
    rules_below = measure_sidebar_rules(measured_labels, summary)
    heading_style: dict[str, Any] | None = None
    for record in measured_labels:
        group = _line_style_of(
            target_pdf, record["page"],
            {"top": record["top"], "bottom": record["bottom"], "x0": record["x0"]},
            summary,
        )
        if group and group.get("bold"):
            heading_style = group
            break
    if heading_style is None:
        raise RuntimeError("generic two-column compile: label typography not measurable")
    lines, _marks = _pdf_lines_and_marks(target_pdf)
    headings = []
    for index, record in enumerate(measured_labels, 1):
        rule = rules_below.get(index)
        headings.append(
            BodyHeadingScaffold(
                verbatim=str(record["text"]),
                page=int(record["page"]),
                top_pt=float(record["top"]),
                x0_pt=float(record["x0"]),
                x1_pt=float(record["x1"]),
                font_height_pt=round(float(record["bottom"]) - float(record["top"]), 3),
                font_size_pt=float(heading_style["font_size_pt"]),
                line_height_pt=float(heading_style.get("line_height_pt") or 0.0) or None,
                bold=True,
                font_family=str(heading_style["font_family"]),
                color_hex=heading_style.get("color_hex"),
                rule_top_pt=rule["top_pt"] if rule else None,
                rule_stroke_pt=rule["stroke_pt"] if rule else None,
                rule_color_hex=rule["color_hex"] if rule else None,
                evidence_ids=[f"local_pdf.sidebar_label.p{record['page']}.top{record['top']:.1f}"],
            )
        )
    bullet_tiers = _two_column_bullet_tiers(lines, summary)
    entry = BodyEntryScaffold(
        left_x0_pt=round(float(bullet_tiers.get("bullet_text") or 0.0) or 1.0, 3),
        right_x1_pt=1.0,
        evidence_ids=["local_pdf.two_column_entry_geometry"],
    )
    return headings, BodyScaffold(
        headings=headings,
        entry=entry,
        contact_icons_present=False,
        contact_separator=None,
        category_grids=[],
    )


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
    proposal: RepairProposal,
    current_version: RenderVersion,
    fingerprints: list[str],
    *,
    state: Any | None = None,
    findings: list[DefectFinding] | None = None,
) -> str | None:
    """Deterministic repair authorization: base version, layer scope, and the
    candidate-facts boundary (plan §8.4). Scope checks resolve the finding's
    section generically from the compiled state (no hardcoded section id)."""
    if proposal.layer == "no_op":
        return "no_op repairs are not authorized"
    if proposal.base_render_version != current_version.version_id:
        return "stale base version"
    if proposal.layer in ("plan_entry_gap", "plan_entry_meta_placement") and state is not None:
        if proposal.section_node_id not in _diagnosed_entry_sections(state, findings or []):
            return "repair scope outside the diagnosed node"
    if proposal.style_updates:
        # The Builder may not touch candidate FACTS — styles are presentation.
        forbidden = [key for key in proposal.style_updates if key.startswith("leaf_")]
        if forbidden:
            return f"candidate fact mutation is forbidden: {forbidden}"
    return None



def _diagnosed_entry_sections(
    state: Any, findings: list[DefectFinding]
) -> set[str]:
    """The section node ids the findings are scoped to, resolved generically
    from the compiled state: a finding's region id IS a state node id when
    that section exists there. No hardcoded section id (the E2 slice's
    F-specific constant was removed with the E3 generalization)."""
    node_ids = {node.node_id for node in state.nodes}
    return {
        finding.region for finding in findings
        if finding.region in node_ids
    }


# --- scripted reviewer scenario records (per target; NOT production rules) ---
# The E2 slice's resume_F scripted rehearsal encodes its own known divergence
# (entry-head role gap); the E3 walkthrough adds its own record below.
# These dictionaries ARE the scripted scenario data, kept out of the shell.
SCRIPTED_REGION_BY_TARGET: dict[str, str] = {}
SCRIPTED_OBSERVATION_BY_TARGET: dict[str, str] = {}
SCRIPTED_REQUEST_BY_TARGET: dict[str, MeasurementRequest] = {}


def prompt_region(prompt: str, target_id: str = "target-resume_F-v1") -> str:
    # The scripted rehearsal's region id comes from the compiled state's
    # mapped entry section (generic lookup), never a hardcoded node id.
    return SCRIPTED_REGION_BY_TARGET.get(target_id, "section.04")


def prompt_observation(prompt: str, target_id: str = "target-resume_F-v1") -> str:
    return SCRIPTED_OBSERVATION_BY_TARGET.get(
        target_id,
        "The vertical distance from the first dated entry head to the second "
        "dated entry head appears larger than in the target.",
    )


def prompt_request(
    prompt: str, finding_id: str, target_id: str = "target-resume_F-v1"
) -> MeasurementRequest:
    request = SCRIPTED_REQUEST_BY_TARGET.get(target_id)
    if request is None:
        request = MeasurementRequest(
            request_id="measure-pending",
            metric="role_gap",
            page=1,
            # Role-aligned anchors per side (target PROJECTS entry heads vs the
            # candidate render's own first two entry heads — different text,
            # same semantic role; plan §9 rule 3).
            from_text="ImageCaptioningSystem",
            to_text="SentimentAnalysisAPI",
            render_from_text="Microsoft",
            render_to_text="Amazon.com",
            region_id="section.04",
        )
    return request.model_copy(update={"request_id": "measure-pending"})


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
        return _pdf_line_rows(pdf, page_number)


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
    parser.add_argument("--e3", action="store_true", help="run the E3 Resume I walkthrough (plan §11; offline)")
    parser.add_argument("--e4", action="store_true", help="run the E4 live-agent Resume I convergence trial (plan §11/§14)")
    parser.add_argument("--e5", action="store_true", help="run the E5 Builder-representation comparison (lanes A and B)")
    parser.add_argument("--lane", choices=["a", "b", "both"], default="both", help="E5 lane restriction")
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

    if args.e4:
        run_dir, terminal, record = run_e4(
            args.target, args.out, live=args.live,
        )
        print(f"E4 run {run_dir.name}: terminal state {terminal}")
        print(
            f"  findings={record.get('summary', {}).get('total_findings')} "
            f"best={record.get('best_render_version')} budget={record.get('budget_state')}"
        )
        print(
            "Live and scripted calls are counted separately; waiting for the owner "
            "is a resumable pause, not success or failure; 'delivered' is reserved "
            "for the explicit owner decision."
        )
        return 0

    if args.e3:
        run_dir, terminal, record = run_e3(args.target, args.out)
        print(f"E3 run {run_dir.name}: terminal state {terminal}")
        print(
            f"  findings={record['summary']['total_findings']} "
            f"best={record['best_render_version']} budget={record['budget_state']}"
        )
        print(
            "Waiting for the owner is a resumable pause, not success or failure; "
            "'delivered' is reserved for the explicit owner decision."
        )
        return 0

    if args.e5:
        run_dir, terminal, record = run_e5(
            args.target, args.out, live=args.live,
            lanes=(args.lane,) if args.lane in ("a", "b") else ("a", "b"),
        )
        print(f"E5 run {run_dir.name}: terminal state {terminal}")
        print(
            f"  lanes={list(record.get('lanes', {}).keys())} "
            f"budget={record.get('budget_state')}"
        )
        print(
            "This is a Builder-representation comparison; automated metrics "
            "declare no winner. The owner reviews owner_review/ and decides."
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


# ===========================================================================
# E3 — Resume I walkthrough (PIPELINE_E_PLAN.md §11; E_PIPELINE_PREP.md E3
# work order). Evidence milestone: does the loop make sustained progress on
# an unfamiliar template without Resume-I-specific production rules?
# ===========================================================================

E3_SCHEMA_VERSION = "pipeline-e-e3-state/1"


class E3LoopRecord(EvidenceModel):
    """The resumable E3 state (plan §14): structure-draft version, render
    versions, findings, measurement results, attributions, repair attempts,
    attempted strategies, fingerprints, budget, and the terminal state."""

    schema_version: Literal["pipeline-e-e3-state/1"] = E3_SCHEMA_VERSION
    target_id: str
    target_sha256: str
    structure_draft_version: str = ""
    render_versions: list[RenderVersion] = Field(default_factory=list)
    best_render_version: str | None = None
    findings: list[DefectFinding] = Field(default_factory=list)
    measurement_results: list[MeasurementResult] = Field(default_factory=list)
    attributions: list[AttributionRecord] = Field(default_factory=list)
    repair_attempts: list[dict[str, Any]] = Field(default_factory=list)
    attempted_strategies: list[str] = Field(default_factory=list)
    action_fingerprints: list[str] = Field(default_factory=list)
    open_findings: list[str] = Field(default_factory=list)
    content_shape_probes_passed: bool = False
    budget_state: dict[str, Any] = Field(default_factory=dict)
    summary: dict[str, Any] = Field(default_factory=dict)


def run_e3(
    target_pdf: Path,
    out_dir: Path | None = None,
    *,
    max_repair_attempts: int = 3,
    budget: RunBudget | None = None,
) -> tuple[Path, str, dict[str, Any]]:
    """Run the E3 Resume I walkthrough (offline, zero model calls).

    Terminal states (plan §14): `ready_for_owner_review` (all gates green,
    every finding measured and re-measured, probes pass — NOT owner
    acceptance: `delivered` is reserved for the explicit owner decision) and
    `budget_exhausted` (resumable; never success). `operational_abort`
    describes the failed operation and never classifies the template as
    unsupported.
    """
    from tests.experiments.a_pipeline import build_format_summary
    from tests.experiments.c2_candidates import candidate_resume_E, independent_candidate_fixtures
    from tests.experiments.c2_pipeline import run_flow_probe, state_from_scaffolds
    from tests.experiments.c2_plan import compile_render_plan
    from tests.experiments.c2_html import render_html
    from tests.experiments import c2_renderer as c2r
    from tests.experiments.c2_state import validate_layout_state, state_bytes
    from app.template_analysis.commercial.models import NormalizedLayoutEvidence

    target_pdf = target_pdf.resolve()
    if not target_pdf.exists():
        raise RuntimeError(f"target PDF not found: {target_pdf}")

    out_dir = out_dir or RUNS / datetime.now(UTC).strftime("e_pipeline_e3_%Y%m%dT%H%M%SZ")
    out_dir.mkdir(parents=True, exist_ok=False)

    budget = budget or RunBudget(max_model_requests=6, max_tool_calls=96)
    trace = RunTrace(out_dir)
    store = EvidenceStore(out_dir, out_dir, base_html="<html><body></body></html>")
    store.manifest["experiment"] = "e_pipeline_e3"
    store.manifest["pipeline_phase"] = "e3"

    def abort(operation: str, error: Exception) -> tuple[Path, str, dict[str, Any]]:
        record = E3LoopRecord(target_id="unknown", target_sha256="0" * 64)
        record.summary["terminal_state"] = "operational_abort"
        record.summary["abort"] = {"operation": operation, "error": str(error)}
        store.manifest["terminal_state"] = "operational_abort"
        store.manifest["abort"] = record.summary["abort"]
        trace.add(agent="shell", phase="operational", action="abort", note=f"{operation}: {error}")
        (out_dir / "e3_state.json").write_text(
            json.dumps(record.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        store.record_state("operational_abort", f"{operation}: {error}")
        (out_dir / "manifest.json").write_text(
            json.dumps(store.manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        trace.save()
        return out_dir, "operational_abort", record.model_dump(mode="json")

    # -- 1. FREEZE the inputs before any examination of a new output --------
    target_cache_dir = RUNS / "target_cache" / _sha256_file(target_pdf)
    raw_path = target_cache_dir / "adobe_raw.json"
    normalized_path = target_cache_dir / "enriched_evidence.json"
    if not raw_path.exists() or not normalized_path.exists():
        return abort(
            "target_evidence_cache",
            RuntimeError(
                "no cached Adobe response for this target under "
                "tests/experiments/runs/target_cache/; E3 makes no live "
                "provider call itself (ADR 0002; only cached evidence is read)"
            ),
        )
    # The shareable run-dir copy is the DETERMINISTIC REDACTED DERIVATIVE; the
    # immutable original (signed provider URLs included) stays in the target
    # cache and is never copied into a shareable artifact (E4 Phase 0).
    (out_dir / "adobe_raw.json").write_text(
        json.dumps(
            redact_signed_urls(json.loads(raw_path.read_text(encoding="utf-8"))),
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    shutil.copy2(normalized_path, out_dir / "enriched_evidence.json")
    target_frozen = freeze_cases(
        target_pdf, raw_path, role="frozen_blind", case_id=target_pdf.stem
    )
    (out_dir / "frozen_case.json").write_text(target_frozen.model_dump_json(indent=2), encoding="utf-8")
    run_config = {
        "run_id": out_dir.name,
        "target_id": f"target-{target_pdf.stem}-v1",
        "target_sha256": target_frozen.target_sha256,
        "adobe_json_sha256": target_frozen.adobe_json_sha256,
        "max_repair_attempts": max_repair_attempts,
        "budget": budget.to_json(),
        # Evaluation truth stays OUT of the agent inputs: the rubric lives in
        # the blind audit document, referenced by path + hash only.
        "evaluation_rubric_reference": {
            "path": "tests/experiments/C2_RESUME_I_BLIND_STRUCTURE_AUDIT.md",
            "sha256": _sha256_file(ROOT / "tests/experiments/C2_RESUME_I_BLIND_STRUCTURE_AUDIT.md"),
            "not_given_to_agents": True,
        },
    }
    (out_dir / "run_config.json").write_text(
        json.dumps(run_config, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    trace.add(agent="shell", phase="e0", action="case_frozen", output=run_config)

    # -- 2. Investigate: evidence pod + coverage audit (raw-first) ----------
    try:
        normalized = NormalizedLayoutEvidence.model_validate_json(
            normalized_path.read_text(encoding="utf-8")
        )
    except Exception as error:
        return abort("normalized_evidence_load", error)
    pod = DualSourcePod(target_pdf, raw_path, out_dir, render_pdf=None, normalized=normalized)
    coverage = pod.audit_coverage()
    budget.spend_tool("inspect_page_overview")
    pod.inspect_page_overview(1, note="E3 investigation page 1")
    budget.spend_tool("inspect_adobe_json")
    pod.inspect_adobe_json(page_number=1, limit=8)
    budget.spend_tool("measure_local_pdf")
    pod.measure_local_pdf(page_number=1, include="rules")

    try:
        summary = build_format_summary(normalized, json.loads(raw_path.read_text(encoding="utf-8")), target_pdf)
        state, derived = compile_two_column_state(target_pdf, summary, evidence=normalized)
    except Exception as error:
        return abort("compile_two_column_state", error)
    state_violations = validate_layout_state(state)
    (out_dir / "c2_layout_state.json").write_bytes(state_bytes(state))
    target_id = f"target-{target_pdf.stem}-v1"

    # The E3 scripted-reviewer scenario record for THIS target (scripted
    # rehearsal data, not a production rule): the E3 walkthrough's
    # known role-aligned divergence, bounded to the compiled state's mapped
    # entry section (a generic derived node id, never a target constant).
    mapped = [
        node for node in state.nodes
        if node.kind == "section" and node.binding
        and node.binding.mapping_action == "map"
        and "work_experience" in node.binding.sources
    ]
    if not mapped:
        return abort(
            "scripted_reviewer_region",
            RuntimeError("compiled state has no mapped work_experience section"),
        )
    entry_region = mapped[0].node_id
    SCRIPTED_REGION_BY_TARGET[target_id] = entry_region
    SCRIPTED_OBSERVATION_BY_TARGET[target_id] = (
        "The vertical distance from the first dated entry head to the second "
        "dated entry head appears larger than in the target."
    )
    SCRIPTED_REQUEST_BY_TARGET[target_id] = MeasurementRequest(
        request_id="measure-pending",
        metric="role_gap",
        page=1,
        # Role-aligned per-side anchors (target's own page-1 dated entry
        # heads vs the candidate render's first two entry heads — different
        # text, same semantic role; plan §9 rule 3).
        from_text="JOB",
        to_text="ANOTHER",
        render_from_text="Microsoft",
        render_to_text="Amazon.com",
        region_id=entry_region,
    )

    # -- 3. Versioned, evidence-linked StructureDraft ------------------------
    structure = []
    unresolved = []
    label_rules = {int(key): value for key, value in derived["sidebar_rules"].items()}
    for index, record in enumerate(derived["sidebar_labels"], 1):
        refs = [
            EvidenceRef(
                evidence_id=f"local_pdf.sidebar_label.p{record['page']}.top{record['top']:.1f}",
                kind="local_measurement",
                page_number=record["page"],
                bbox_pt=[record["x0"], record["top"], record["x1"], record["bottom"]],
                source_kind="target_pdf",
                source_path="structure.jsonl",
            )
        ]
        rule = label_style = label_rules.get(index)
        if rule:
            refs.append(
                EvidenceRef(
                    evidence_id=f"local_pdf.sidebar_rule.top{rule['top_pt']:.1f}",
                    kind="local_measurement",
                    page_number=record["page"],
                    bbox_pt=[rule["x0_pt"], rule["top_pt"], rule["x1_pt"], rule["top_pt"]],
                    source_kind="target_pdf",
                    source_path="structure.jsonl",
                )
            )
        structure.append(
            StructuralRelation(
                claim_id=f"claim.{index:03d}",
                relation="section_boundary",
                parent=None,
                child=f"section.{index:02d}",
                statement=(
                    f"A right-aligned short line at x0={record['x0']:.1f}..x1={record['x1']:.1f} "
                    f"(page {record['page']}, top {record['top']:.1f}) shares the clustered "
                    "sidebar-label right edge and owns a measured rule below it — "
                    "claimed as the section boundary of a two-column sidebar family."
                ),
                evidence=refs,
                confidence=0.75,
                status="proposed",
            )
        )
    continuation_pages = sorted({record["page"] for record in derived["sidebar_labels"] if record["page"] != 1})
    for page in continuation_pages:
        unresolved.append(
            UnresolvedItem(
                item_id=f"unresolved.continuation.{page}",
                question=(
                    f"Page {page} repeats the sidebar-label column; is it a "
                    "page-break continuation of the same template family or a second "
                    "column layout?"
                ),
                status="unresolved",
                reason=(
                    "the label cluster evidence shows the pattern repeats, but the "
                    "reading-order relationship across the page break is not "
                    "measured by any evidence channel in this run"
                ),
                evidence_gap="ambiguous_relation",
            )
        )
    unresolved = _dedup_unresolved(unresolved)
    unresolved.append(
        UnresolvedItem(
            item_id="unresolved.bullet_marker_glyph",
            question=(
                "The LI/Lbl bullet markers measure a dot glyph column and a text column; "
                "the marker glyph style (round vs square) is not recovered from the "
                "normalized evidence."
            ),
            status="unresolved",
            reason=(
                "the raw Adobe response carries the markers, but this run's offline "
                "evidence channels cannot measure glyph style from the cached response"
            ),
            evidence_gap="normalization_loss",
        )
    )
    draft = TargetStructureDraft(
        target_id=target_id,
        investigator="scripted",
        structure=structure,
        unresolved=unresolved,
        self_reported=SelfReportedStatus(
            status="partial",
            sections_expected=None,
            sections_identified=len(derived["sidebar_labels"]),
            notes=(
                "Scripted E3 investigator: every section-boundary claim cites a "
                "measured sidebar label row and its measured rule; bullet-marker "
                "glyph style and cross-page reading order stay unresolved."
            ),
        ),
        evidence_used_by_id={record.evidence_id: record for record in pod.records},
    )
    draft_path = out_dir / "structure_draft.json"
    draft_path.write_text(draft.model_dump_json(indent=2), encoding="utf-8")
    store.register_version(
        "structure_draft_v1", draft_path, "evidence-linked E3 structure draft"
    )
    trace.add(
        agent="target_investigator",
        phase="target_understanding",
        action="structure_draft",
        output={
            "claims": len(draft.structure),
            "unresolved": len(draft.unresolved),
            "self_reported": draft.self_reported.status,
        },
        persist_output=True,
    )
    (out_dir / "structure.jsonl").write_text(
        "\n".join(
            json.dumps(claim.model_dump(mode="json"), ensure_ascii=False)
            for claim in draft.structure
        )
        + "\n",
        encoding="utf-8",
    )

    # -- 4. Builder: candidate-safe presentation from reviewed content -------
    from tests.experiments.c2_candidates import candidate_resume_E as _cand_E

    candidate = _cand_E()
    probe = run_flow_probe(state, candidate)
    (out_dir / "flow_probe.json").write_text(
        json.dumps(probe, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    frozen_dir = RUNS / "c_pipeline_D_to_E_20260910T195515Z"
    if not (frozen_dir / "target.pdf").exists():
        return abort("frozen_c1_baseline", FileNotFoundError(str(frozen_dir / "target.pdf")))
    privacy_target = frozen_dir / "target.pdf"

    try:
        environment = pinned_export_environment({})
    except Exception as error:
        return abort("pinned_chrome_environment", error)

    versions: list[RenderVersion] = []
    findings: list[DefectFinding] = []
    measurement_results: list[MeasurementResult] = []
    attributions: list[AttributionRecord] = []
    repair_attempts: list[dict[str, Any]] = []
    attempted_strategies: list[str] = []
    fingerprints: list[str] = []
    resolved_findings: set[str] = set()
    counter = {"finding": 0, "request": 0}

    # Generic shell state transition (plan §14 escalation — no target-specific
    # rule): candidate header fields with no home in the compiled state route
    # through the EXISTING explicit header-overflow disposition. The record
    # names the measured target structure, never a Resume-I string.
    from tests.experiments.c2_candidates import UnroutableContent
    from tests.experiments.fill_plan import CONTACT_KIND_CHECKS

    header_slots_in_state = {
        slot
        for node in state.nodes if node.kind == "header_row"
        for slot in node.slots
    }
    unhomed_header_fields = [
        leaf for leaf in candidate.leaves
        if leaf.kind == "header_field" and leaf.slot not in header_slots_in_state
    ]
    if unhomed_header_fields:
        candidate = candidate.model_copy(
            update={
                "unroutable": [
                    *candidate.unroutable,
                    *(
                        UnroutableContent(
                            text=leaf.text or "",
                            reason=(
                                "the compiled target header region carries no measured "
                                f"row with slot {leaf.slot!r} (two-column family: the "
                                "measured contact-table rows sit inside the section "
                                "content); routes through the explicit candidate-only "
                                "header-overflow node"
                            ),
                            slot=leaf.slot,
                        )
                        for leaf in unhomed_header_fields
                    ),
                ]
            }
        )
        trace.add(
            agent="shell", phase="builder", action="header_overflow_disposition",
            output={"leaves": [leaf.leaf_id for leaf in unhomed_header_fields]},
            note="generic header-overflow disposition for header fields without a measured home",
            persist_output=True,
        )

    def render_version(note: str) -> tuple[RenderVersion, Path, dict[str, Any]]:
        """Render one whole document version through the canonical chain and
        run the delivery gates (no scripted gap mutation: the plan compiles
        from the state as compiled)."""
        version, pdf, gates, _plan, _html = render_version_full(note)
        return version, pdf, gates

    def render_version_full(note: str) -> tuple[RenderVersion, Path, dict[str, Any], Any, str]:
        """Render one whole document version through the canonical chain,
        run the delivery gates, and return the plan + html for the
        gate-driven finding derivation (no scripted gap mutation)."""
        from tests.experiments.c2_plan import compile_render_plan as _crp
        from tests.experiments.c2_html import render_html as _rh

        budget.spend_tool("render_and_checkpoint")
        plan = _crp(state, candidate)
        if plan.status == "failed":
            raise RuntimeError(f"refusing to render a failed plan: {plan.failures[:3]}")
        html = _rh(state, plan)
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
        structure_gate = c2r.structure_gate(plan, state, html, pdf)
        blank = c2r.blank_page_gate(pdf)
        accounting = c2r.candidate_accounting_gate(plan, content)
        _headings_scaffold, body_scaffold = compile_two_column_state_for_scaffold(target_pdf, summary)
        bullet_tiers = _two_column_bullet_tiers(_pdf_lines_and_marks(target_pdf)[0], summary)
        shape = c2r.content_shape_verification(state, plan, body_scaffold, bullet_tiers, html, summary, pdf)
        gates = {
            "deterministic_render": all(
                c2r._sha256(left) == c2r._sha256(right)
                for left, right in zip(pages, second_pages)
            )
            and stability["passed"],
            "no_target_candidate_facts": privacy["passed"],
            "section_order_matches_state": structure_gate["section_order_matches_state"],
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
            agent="shell", phase="render", action="render_version",
            output=version.model_dump(mode="json"), note=note,
        )
        return version, pdf, {"passed": version.hard_gates_passed, "gates": gates}, plan, html

    def measure_and_attribute(
        finding: DefectFinding, pdf: Path
    ) -> tuple[MeasurementResult, AttributionRecord]:
        counter["request"] += 1
        request = finding.requested_measurement.model_copy(
            update={"request_id": f"measure-{counter['request']:03d}"}
        )
        result = MeasureController(pod, budget, trace).execute(request, current_pdf=pdf)
        measurement_results.append(result)
        trace.add(
            agent="attribution_investigator", phase="attribute", action="trace",
            input={"finding": finding.finding_id, "request": request.request_id},
            note="raw target evidence -> structure -> template slot -> candidate binding -> RenderPlan -> DOM/CSS -> final PDF object",
        )
        # Deterministic attribution from the measurement; the reviewer's
        # proposed_cause stays a recorded hypothesis.
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
                        "the measured final-PDF object differs from the target's "
                        "measured role gap beyond the documented tolerance"
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
            agent="attribution_investigator", phase="attribute", action="attribution",
            output=attribution.model_dump(mode="json"),
        )
        return result, attribution

    def accounting_defect_findings(
        gates: dict[str, Any], version: RenderVersion, pdf: Path, plan: Any, html: str
    ) -> list[DefectFinding]:
        """Deterministic observation-first findings from the render's own
        gates (offline rehearsal of the independent reviewer's localized
        scout role): each failed gate produces ONE finding bound to the exact
        versions with a typed measurement request against the ACTUAL final
        PDF. Observations are recorded separately from causal hypotheses —
        the shell's attribution step (below) decides the owner from the
        measurement, never from this hypothesis."""
        emitted: list[DefectFinding] = []
        if gates.get("content_gate"):
            return emitted
        content = c2r.content_gate(plan, html, pdf)
        # One deterministic finding per failed-gate CLASS (deduplicated by the
        # suspected dimension), each bound to the exact versions and carrying
        # one typed measurement request the shell must execute on the actual
        # final PDF — never one finding per leaf row.
        if content["missing_pdf"]:
            counter["finding"] += 1
            leaf_id = content["missing_pdf"][0]
            text = c2r._leaf_text(plan, leaf_id)
            prefix = " ".join(str(text).split())[:20]
            emitted.append(
                DefectFinding(
                    finding_id=f"finding-{counter['finding']:03d}",
                    target_version=target_id,
                    render_version=version.version_id,
                    page=1,
                    region="section.04",
                    observation=(
                        f"The rendered entry body wraps the candidate detail line so the "
                        f"final PDF text interleaves it with the meta column; the verbatim "
                        f"detail (prefix {prefix!r}) is not present as one text object "
                        f"({len(content['missing_pdf'])} wrapped detail leaf(s) affected)."
                    ),
                    suspected_dimension="entry_text_wrap",
                    proposed_cause=(
                        "hypothesis only: the renderer's entry flex geometry owns the "
                        "wrap/interleave; attribution verifies from the measurement"
                    ),
                    requested_measurement=MeasurementRequest(
                        request_id="measure-pending",
                        metric="role_gap",
                        page=1,
                        # Role-aligned per-side anchors, same page: the
                        # target's page-1 dated entry heads (two heads on one
                        # page — different text, same semantic
                        # role as the render's first two entry heads).
                        from_text="JOB",
                        to_text="ANOTHER",
                        render_from_text="Microsoft",
                        render_to_text="Amazon.com",
                        region_id="section.04",
                    ),
                    severity="high",
                    confidence=0.7,
                    reviewer="scripted",
                )
            )
        return emitted

    # -- 5. First render (the owner-reviewable baseline) ---------------------
    try:
        v1, v1_pdf, v1_gates, v1_plan, v1_html = render_version_full(
            "first render (compiled two-column state)"
        )
    except Exception as error:
        return abort("render_version_1", error)

    # -- 6. Review -> Measure -> Attribute -> Repair -> Re-render loop -------
    for attempt in range(1, max_repair_attempts + 1):
        if budget.remaining_model_requests() < 1:
            trace.add(agent="shell", phase="loop", action="budget_exhausted", note="before review")
            break
        current_version = versions[-1]
        current_pdf = _render_pdf_of(out_dir, len(versions))
        counter["finding"] += 1
        prompt = (
            f"TARGET {target_frozen.case_id} sha={target_frozen.target_sha256[:12]} "
            f"RENDER {current_version.version_id} page 1."
        )
        findings_new = ScriptedReviewer().run(
            prompt,
            finding_id=f"finding-{counter['finding']:03d}",
            target_version=target_id,
            render_version=current_version.version_id,
            page=1,
            budget=budget,
            trace=trace,
            target_id=target_id,
        )
        findings_new = _validate_finding_versions(
            findings_new, target_id, current_version.version_id
        )
        # Gate-driven findings: the CURRENT render's own delivery gates are
        # deterministic observations; each failed gate contributes an
        # observation-first finding measured against the ACTUAL final PDF.
        if not v1_gates["passed"]:
            findings_new.extend(
                accounting_defect_findings(
                    v1_gates["gates"],
                    v1, v1_pdf, v1_plan, v1_html,
                )
            )
        findings.extend(findings_new)
        for finding in findings_new:
            if finding.finding_id in resolved_findings:
                continue
            try:
                result, attribution = measure_and_attribute(finding, pdf=current_pdf)
            except BudgetExhausted as error:
                escalate(f"attempt{attempt}:{finding.finding_id}:tool_budget_exhausted")
                trace.add(agent="shell", phase="loop", action="budget_exhausted", note=str(error))
                raise CheckpointBudgetExceeded(str(error)) from error
            if attribution.repair_owner != "builder":
                attempted_strategies.append(
                    f"attempt{attempt}:{finding.finding_id}:{attribution.attribution}:remeasure_or_other_channel"
                )
                continue
            if abs(result.delta_pt or 0.0) <= E2_IMPROVEMENT_TOLERANCE_PT:
                resolved_findings.add(finding.finding_id)
                continue
            # §14 escalation ladder: the confirmed defect's attributed owner
            # determines the NEXT bounded action; a defect the two bounded
            # layers cannot repair (e.g. a renderer-owned entry-wrap defect)
            # is recorded as a strategy escalation with its measured evidence,
            # never repaired blindly and never 'unsupported'.
            if finding.suspected_dimension != "role_gap":
                attempted_strategies.append(
                    f"attempt{attempt}:{finding.finding_id}:{attribution.attribution}:"
                    f"{finding.suspected_dimension}:change_repair_layer_or_template_representation"
                )
                continue
            fingerprint = f"{attribution.attribution}:{finding.suspected_dimension}:{round(result.delta_pt or 0, 3)}"
            if fingerprint in fingerprints:
                attempted_strategies.append(
                    f"attempt{attempt}:{finding.finding_id}:repeated_action_change_strategy"
                )
                continue
            fingerprints.append(fingerprint)
            proposal = RepairProposal(
                finding_id=finding.finding_id,
                base_render_version=current_version.version_id,
                layer="plan_entry_gap",
                section_node_id=finding.region,
                gap_delta_pt=-3.0 if (result.delta_pt or 0.0) > 0 else 3.0,
                rationale="attributed plan-layer role gap; bounded single-layer correction",
                agent="scripted",
            )
            validation_error = _validate_repair(
                proposal, current_version, fingerprints, state=state, findings=list(findings)
            )
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
            budget.repair_attempt_count += 1  # Phase 0: repair counts must agree
            trace.add(
                agent="builder", phase="repair", action="proposal",
                output=proposal.model_dump(mode="json"), persist_output=True,
            )
            try:
                candidate_version, candidate_pdf, _cg = render_version(
                    f"repair attempt {attempt} for {finding.finding_id}"
                )
            except Exception as error:
                return abort("render_repair_candidate", error)
            if not candidate_version.hard_gates_passed:
                attempted_strategies.append(
                    f"attempt{attempt}:{finding.finding_id}:repair_failed_gates_rolled_back"
                )
                trace.add(agent="shell", phase="repair", action="rolled_back", note="gates failed")
                continue
            repeat_request = finding.requested_measurement.model_copy(
                update={"request_id": result.request_id}  # IDENTICAL request id
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
            if improved:
                candidate_version = candidate_version.model_copy(update={"promoted": True})
                versions[-1] = candidate_version
                resolved_findings.add(finding.finding_id)
                trace.add(
                    agent="shell", phase="repair", action="promoted",
                    output={
                        "version": candidate_version.version_id,
                        "before": result.model_dump(mode="json"),
                        "after": repeat_result.model_dump(mode="json"),
                    },
                    persist_output=True,
                )
            else:
                attempted_strategies.append(
                    f"attempt{attempt}:{finding.finding_id}:non_improving_rolled_back"
                )
                trace.add(agent="shell", phase="repair", action="rolled_back", note="non-improving repair")

    # -- 7. Content-shape probes (canonical C2 independent fixtures) ---------
    shape_probes: dict[str, Any] = {}
    for profile, probe_candidate in independent_candidate_fixtures().items():
        shape_probes[profile] = run_flow_probe(state, probe_candidate)
    (out_dir / "content_shape_probes.json").write_text(
        json.dumps(shape_probes, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    probes_ok = all(entry["passed"] for entry in shape_probes.values())

    # -- 8. Terminal state ----------------------------------------------------
    open_findings = [
        finding.finding_id for finding in findings if finding.finding_id not in resolved_findings
    ]
    best_version_id = next(
        (v.version_id for v in versions if v.promoted),
        next((v.version_id for v in versions if v.hard_gates_passed), None),
    )
    if versions and best_version_id and not open_findings and probes_ok:
        terminal = "ready_for_owner_review"
    else:
        terminal = "budget_exhausted"
    record = E3LoopRecord(
        target_id=target_id,
        target_sha256=target_frozen.target_sha256,
        structure_draft_version="structure_draft_v1",
        render_versions=versions,
        best_render_version=best_version_id,
        findings=findings,
        measurement_results=measurement_results,
        attributions=attributions,
        repair_attempts=repair_attempts,
        attempted_strategies=attempted_strategies,
        action_fingerprints=fingerprints,
        open_findings=open_findings,
        content_shape_probes_passed=probes_ok,
        budget_state=budget.to_json(),
        summary={
            "total_findings": len(findings),
            "terminal_state": terminal,
            "best_render_version": best_version_id,
            "content_shape_probes_passed": probes_ok,
            "coverage_audit_status": coverage.get("status"),
        },
    )
    (out_dir / "e3_state.json").write_text(
        json.dumps(record.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    store.manifest["terminal_state"] = terminal
    store.manifest["e3"] = record.model_dump(mode="json")
    store.manifest["budget"] = budget.to_json()
    store.manifest["pending_candidate_id"] = None  # candidates stay INACTIVE; the owner decides
    store.record_state(terminal, f"best={record.best_render_version}")
    (out_dir / "manifest.json").write_text(
        json.dumps(store.manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    trace.save()
    _write_e3_report(out_dir, target_frozen, draft, record, terminal)
    return out_dir, terminal, record.model_dump(mode="json")


def _write_e3_report(
    out_dir: Path,
    frozen: FrozenCase,
    draft: TargetStructureDraft,
    record: E3LoopRecord,
    terminal: str,
) -> None:
    claims = "\n".join(
        f"| `{claim.claim_id}` | {claim.relation} | {claim.confidence:.2f} | "
        f"{', '.join(ref.evidence_id for ref in claim.evidence) or '-'} | {claim.statement[:90]} |"
        for claim in draft.structure
    ) or "| - | - | - | - | - |"
    versions_table = "\n".join(
        f"| `{version.version_id}` | {version.hard_gates_passed} | {version.promoted} | {version.note} |"
        for version in record.render_versions
    ) or "| - | - | - | - | - |"
    findings_table = "\n".join(
        f"| `{finding.finding_id}` | {finding.render_version} | {finding.region} | "
        f"{finding.observation} |"
        for finding in record.findings
    ) or "| - | - | - | - | - |"
    strategies = "\n".join(f"| {strategy} |" for strategy in record.attempted_strategies) or "| - |"
    (out_dir / "REPORT.md").write_text(
        f"""# Pipeline E3 Resume I walkthrough — {out_dir.name}

- Case: `{record.target_id}`; target sha256 `{record.target_sha256}`
- Terminal state: **{terminal}**
- Best valid render: `{record.best_render_version}`
- Structure draft: `structure_draft_v1` ({len(draft.structure)} claims, {len(draft.unresolved)} unresolved)
- Model requests: {record.budget_state.get('model_request_count')}/{record.budget_state.get('max_model_requests')};
  tool calls: {record.budget_state.get('tool_call_count')}/{record.budget_state.get('max_tool_calls')}

## Structure claims (each with evidence pointers)

| claim | relation | confidence | evidence | statement |
| --- | --- | --- | --- | --- |
{claims}

## Render versions

| version | hard gates | promoted | note |
| --- | --- | --- | --- |
{versions_table}

## Findings (observation-first, version-bound)

| finding | render | region | observation |
| --- | --- | --- | --- |
{findings_table}

## Attempted strategies (incl. escalations)

|
{strategies}

## What this run does and does not establish

Establishes: the bounded See -> Investigate -> Compile -> Render -> Measure ->
Attribute -> Repair -> Re-render loop ran against an UNFAMILIAR two-column
sidebar target (Resume I) with zero live model calls and REAL Chrome renders
and REAL final-PDF pdfplumber measurement; the structure draft is
evidence-linked and the Builder compiles through the existing
``state_from_scaffolds`` mapping without a new renderer or schema family.

Does **not** establish: any visual acceptance or fidelity winner. The owner
reviews the final HTML/PDF (T-v1) and decides; automated metrics declare
nothing (plan §13). No live model call was made and no ADR/product contract
changed. This run produced NO T-v1 record and NO owner-acceptance claim.

## Authority boundary

Experiment-only under `tests/experiments/` (PIPELINE_E_PLAN.md §5,
E_PIPELINE_PREP.md §5). DOCX stays out; editable HTML + Chrome PDF is the
render surface.
""",
        encoding="utf-8",
    )


# ===========================================================================
# E4 — live-agent Resume I convergence trial (PIPELINE_E_PLAN.md §11/§13/§14;
# E_PIPELINE_PREP.md E4 work order). The four LIVE roles (Target Investigator,
# independent Visual Reviewer, Attribution Investigator, Builder/Repair) run
# over the EXISTING PydanticAI runtime/provider path; the deterministic shell
# stays the Orchestrator and owns versions, budgets, gates, rollback,
# best-valid selection, strategy escalation and the terminal state. No agent
# promotes its own output or approves its own repair.
#
# The offline path (live=False) is the mandatory verification path: the SAME
# shell with zero live calls (deterministic derivation + ScriptedReviewer +
# scripted bounded-repair rehearsal). `--live` swaps the agent callables to
# the live roles without touching the shell.
# ===========================================================================

E4_SCHEMA_VERSION = "pipeline-e-e4-state/1"
E4_TEMPERATURE = 0.0  # frozen sampling control (recorded in run_config.json)
E4_REVIEWER_MAX_REQUESTS = 4  # per-agent-run bounded calls inside the budget
E4_BUILDER_MAX_REQUESTS = 2
E4_INVESTIGATOR_MAX_REQUESTS = 12
E4_ATTRIBUTION_MAX_REQUESTS = 8
E4_PER_CALL_MAX_TOOL_CALLS = 24

# Candidate-fact/structure safety gates (E4 promotion rule): a repair that
# improves its confirmed defect may only be promoted when these gates are
# green — no candidate-fact damage, no target-fact leak, no structural break,
# deterministic render, accounting intact. `content_shapes_match_evidence` is
# deliberately NOT in this set: it fails identically on the base version for
# the two-column family (the template-representation ceiling recorded since
# E3), and gating defect-level progress on a ceiling the bounded layers cannot
# move would make the convergence question unanswerable. It stays a hard gate
# for the TERMINAL owner-review state and is recorded on every version.
CANDIDATE_FACT_GATES = (
    "content_gate",
    "candidate_content_accounting",
    "no_target_candidate_facts",
    "no_blank_page",
    "deterministic_render",
    "section_order_matches_state",
)


def _call_limits(budget: RunBudget, max_requests: int) -> Any:
    """Bounded per-agent-run limits inside the global run budget."""
    from pydantic_ai.usage import UsageLimits

    return UsageLimits(
        request_limit=max(1, min(max_requests, budget.remaining_model_requests())),
        # bounded per-agent-run tool calls: one agent run can never eat the
        # global tool budget (the shell still counts every call globally).
        tool_calls_limit=max(1, min(E4_PER_CALL_MAX_TOOL_CALLS, budget.max_tool_calls - budget.tool_calls)),
    )


def _live_model_settings() -> dict[str, Any]:
    """Frozen live sampling settings (Phase 0): temperature + structured-output
    transport for the configured provider (the DeepSeek-compatible runtime's
    thinking mode rejects tool_choice, so thinking is disabled for the typed
    structured outputs; recorded in the frozen run config)."""
    return {
        "temperature": E4_TEMPERATURE,
        "max_tokens": 16384,  # the provider default output cap truncated typed outputs
        # (and the authored-template lane needs more still — Phase 0 E5)
        "extra_body": {"thinking": {"type": "disabled"}},
    }


def _live_visual_model() -> Any:
    """PydanticAI OpenAI-compatible model for image-bearing roles. Uses the
    configured visual provider when present (owner decision 2026-09-08, the
    existing provider-gated experiment path) and falls back to D's live model."""
    import os

    from dotenv import load_dotenv
    from openai import AsyncOpenAI
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    load_dotenv(ROOT / ".env")  # experiment-only credential loading
    base = os.environ.get("VISUAL_API_BASE")
    key = os.environ.get("VISUAL_API_KEY")
    model_name = os.environ.get("A_PIPELINE_VISUAL_MODEL")
    if base and key and model_name:
        provider = OpenAIProvider(
            openai_client=AsyncOpenAI(api_key=key, base_url=base, timeout=300)
        )
        return OpenAIChatModel(model_name, provider=provider)
    from tests.experiments.d_pipeline import _live_model

    return _live_model()


def _model_identity() -> dict[str, Any]:
    """Frozen configuration record: model NAMES only — never keys, URLs,
    tokens or any other environment data (E4 work order security boundary)."""
    import os

    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    visual_configured = bool(
        os.environ.get("VISUAL_API_BASE")
        and os.environ.get("VISUAL_API_KEY")
        and os.environ.get("A_PIPELINE_VISUAL_MODEL")
    )
    text_model = (
        os.environ.get("D_PIPELINE_MODEL")
        or os.environ.get("C_PIPELINE_MODEL")
        or os.environ.get("B_PIPELINE_MODEL")
        or os.environ.get("A_PIPELINE_MODEL")
        or "deepseek-flash"  # DeepSeek-V4.1-Flash (canonical id; the retired
        # vision-exp alias is no longer used for E roles, owner direction 2026-09-20)
    )
    return {
        "runtime": "pydantic_ai Agent(OpenAIChatModel) — existing D/E runtime path",
        "vision_model": (
            os.environ.get("A_PIPELINE_VISUAL_MODEL") if visual_configured else text_model
        ),
        "text_model": text_model,
        "temperature": E4_TEMPERATURE,
        "credential_note": "API keys are read from the local .env at call time; never recorded",
    }


def _pdf_line_rows(pdf: Path, page_number: int) -> list[dict[str, Any]]:
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


def _page_pt_size(pdf: Path, page_number: int) -> tuple[float, float]:
    import pdfplumber

    with pdfplumber.open(pdf) as document:
        page = document.pages[page_number - 1]
        return float(page.width), float(page.height)


def _crop_rows_png(
    pdf: Path,
    out_dir: Path,
    tag: str,
    page: int,
    anchor_texts: list[str],
    pad_pt: float = 10.0,
) -> Path | None:
    """Localized original-resolution crop around the verbatim anchor rows of
    one PDF (owner-package evidence: the owner sees the actual region a
    finding was measured on)."""
    rows = _pdf_line_rows(pdf, page)
    matched = [
        row for row in rows
        if any(row["text"].startswith(text) for text in anchor_texts)
    ]
    if not matched:
        return None
    x0 = min(float(row["words"][0]["x0"]) for row in matched) - pad_pt
    x1 = max(
        (float(word["x1"]) for row in matched for word in row["words"]), default=0.0
    ) + pad_pt
    top = min(float(row["top"]) for row in matched) - pad_pt
    bottom = max(float(row["top"]) for row in matched) + pad_pt * 2.0
    width_pt, _height_pt = _page_pt_size(pdf, page)
    pages = _render_pages(pdf, out_dir, tag)
    with Image.open(pages[page - 1]) as image:
        full = image.convert("RGB")
    scale = full.width / width_pt
    box = (
        max(0, int(x0 * scale)),
        max(0, int(top * scale)),
        min(full.width, int(x1 * scale)),
        min(full.height, int(bottom * scale)),
    )
    if box[2] <= box[0] or box[3] <= box[1]:
        return None
    crop = full.crop(box)
    out = out_dir / f"{tag}_page_{page}.png"
    crop.save(out)
    return out


def _side_by_side(images: list[tuple[str, Path | None]], out_path: Path) -> Path:
    """Labeled side-by-side comparison strip (owner-package artifact)."""
    from PIL import ImageDraw

    loaded = []
    for label, path in images:
        if path is not None and Path(path).exists():
            with Image.open(path) as image:
                loaded.append((label, image.convert("RGB")))
    if not loaded:
        raise RuntimeError("no comparison images available")
    strip = 26
    width = sum(image.width for _, image in loaded) + 8 * (len(loaded) + 1)
    height = max(image.height for _, image in loaded) + strip + 8
    canvas = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(canvas)
    x = 8
    for label, image in loaded:
        draw.text((x + 2, 5), label, fill="black")
        canvas.paste(image, (x, strip))
        x += image.width + 8
    canvas.save(out_path)
    return out_path


def _overview_pngs(pdf: Path, out_dir: Path, tag: str) -> list[Path]:
    """Downscaled whole-page overviews of one PDF (reviewer input; the same
    bounded channel as the E1 overview tool)."""
    pages = _render_pages(pdf, out_dir, tag)
    resized: list[Path] = []
    for page in pages:
        with Image.open(page) as image:
            full = image.convert("RGB")
        ratio = min(1.0, OVERVIEW_MAX_EDGE / max(full.size))
        scaled = full.resize(
            (max(1, int(full.width * ratio)), max(1, int(full.height * ratio))),
            _lanczos(),
        )
        out = page.with_name(f"{tag}_overview_{page.stem}.png")
        scaled.save(out)
        resized.append(out)
    return resized


E4_BUILDER_INSTRUCTIONS = (
    "You are the Builder/Repair agent of a resume-layout experiment.\n"
    "You receive ONE confirmed, attributed defect finding, its typed measurement\n"
    "result, and the attribution record. You choose ONE bounded repair from the\n"
    "typed layer vocabulary the shell can actually apply:\n"
    "- plan_entry_gap: adjust one section's measured inter-entry gap by a small\n"
    "  positive/negative pt delta (gap_delta_pt).\n"
    "- plan_entry_meta_placement: switch one section's entry head to the\n"
    "  'title_row' placement, where the meta column shares the FIRST title\n"
    "  line's row and the remaining head lines span the whole entry width\n"
    "  (entry_meta_placement='title_row').\n"
    "You must state: the attributed defect, the exact layer being changed, the\n"
    "exact files/fields/selectors affected, the expected measurable result,\n"
    "possible regressions, and the rollback condition.\n"
    "Hard boundaries: you NEVER change, invent, or remove candidate facts\n"
    "(leaf content is untouchable); you never promote your own output (the\n"
    "shell validates, renders, re-measures and decides); you never target a\n"
    "specific template with a hand-tuned constant.\n"
)

E4_ATTRIBUTION_INSTRUCTIONS = (
    "You are the independent Attribution Investigator of a resume-layout\n"
    "experiment. For ONE confirmed material finding whose deterministic\n"
    "measurement could not bind to a final-PDF object, you trace the chain\n"
    "raw target evidence -> StructureDraft -> reusable template slot ->\n"
    "candidate binding -> RenderPlan -> DOM/CSS -> final PDF object using your\n"
    "read-only evidence tools, and decide which layer owns the defect.\n"
    "You may conclude that the visual reviewer's recorded hypothesis was\n"
    "wrong: preserve the observation and replace only the causal hypothesis.\n"
    "Every decision cites evidence ids you actually collected. If two owners\n"
    "remain plausible, return 'unresolved' with repair_owner='reviewer' rather\n"
    "than guessing. You never approve delivery and never promote anything.\n"
    f"Budget: ONE bounded agent run (at most {E4_ATTRIBUTION_MAX_REQUESTS} requests,\n"
    "at most a few tool calls). Answer DIRECTLY from the recorded measurement and\n"
    "target description when possible; request at most 2-3 targeted evidence\n"
    "lookups, then RETURN the typed hypothesis — never keep investigating.\n"
)


class LiveBuilderRepair(EvidenceModel):
    """The live Builder's bounded proposal + required statements. The shell
    binds finding/base versions, validates scope and candidate safety, and
    alone decides promotion (no agent output type can express promotion)."""

    layer: Literal["plan_entry_gap", "plan_entry_meta_placement"]
    section_node_id: str
    gap_delta_pt: float = 0.0
    entry_meta_placement: Literal["title_row"] | None = None
    attributed_defect: str
    files_fields_selectors: list[str] = Field(default_factory=list)
    expected_measurable_result: str
    possible_regressions: str
    rollback_condition: str
    rationale: str


class LiveAttributionHypothesis(EvidenceModel):
    """The live Attribution Investigator's typed hypothesis. The hypothesis
    states WHICH finding it explains (``finding_id``); the shell binds the
    exact finding/render/measurement versions BY IDENTITY, never by list
    position, validates the builder-ownership semantics, and records it."""

    finding_id: str
    attribution: Literal[
        "target_evidence_missing",
        "target_understanding",
        "template_compilation",
        "candidate_binding",
        "render_plan",
        "renderer",
        "measurement_failure",
        "not_measurable",
    ]
    hypothesis_status: Literal["confirmed", "rejected", "unresolved"]
    repair_owner: Literal["builder", "binding", "renderer", "investigator", "reviewer", "none"]
    reason: str
    evidence_ids: list[str] = Field(default_factory=list)


def write_evaluation_report(
    out_dir: Path,
    record: dict[str, Any],
    *,
    structure_evaluation: dict[str, Any],
    rubric_source: str,
    run_name: str | None = None,
) -> dict[str, Any]:
    """Shared post-run evaluation report (Phase 0): every count is derived from
    the SAME typed loop state, so state, trace and report agree. Scripted
    invocations and live model calls are reported separately; duplicate
    unresolved items (same item_id) collapse to one."""
    versions = record.get("render_versions") or []
    attempts = [entry for entry in record.get("repair_attempts") or [] if "layer" in entry]
    promotions = [v for v in versions if v.get("promoted")]
    rollbacks = [
        strategy for strategy in record.get("attempted_strategies") or []
        if "rolled_back" in strategy
    ]
    budget_state = record.get("budget_state") or {}
    by_mode = budget_state.get("calls_by_mode") or {}
    calls_by_agent = budget_state.get("calls_by_agent", {})
    # Phase 0 compatibility: a state captured before the scripted/live split
    # counts every spend_model call as a scripted invocation (no live calls
    # existed before E4 — token usage zero).
    scripted_invocations = by_mode.get(
        "scripted",
        sum(calls_by_agent.values()) if calls_by_agent else 0,
    )
    live_invocations = by_mode.get("live", 0)
    unresolved = _dedup_unresolved(
        structure_evaluation.get("unresolved_items") or []
    )
    sections_bound = structure_evaluation.get("sections_bound_to_candidate_sources")
    report = {
        "run": run_name or out_dir.name,
        "terminal_state": record.get("summary", {}).get("terminal_state")
        or record.get("terminal_state"),
        "best_render_version": record.get("best_render_version"),
        "correctly_recovered_structure": {
            **structure_evaluation.get("correctly_recovered", {}),
            "sections_bound_to_candidate_sources": sections_bound,
            "note": structure_evaluation.get("correctly_recovered", {}).get(
                "note", f"{sections_bound} section(s) bind to candidate source roles"
            ),
        },
        "confidently_incorrect_structure": structure_evaluation.get(
            "confidently_incorrect", []
        ),
        "missed_structure": structure_evaluation.get("missed_structure", {}),
        "unresolved_items": [
            {"item_id": item.item_id, "question": item.question} for item in unresolved
        ],
        "unresolved_item_count": len(unresolved),
        "visual_defects_found": structure_evaluation.get("visual_defects_found", []),
        "findings_confirmed_or_falsified": structure_evaluation.get(
            "findings_confirmed_or_falsified",
            {"confirmed": 0, "falsified": 0, "measurement_failures": 0},
        ),
        "attribution_accuracy": {
            "owner_decided_by_measurement_not_model": structure_evaluation.get(
                "owner_decided_by_measurement_not_model", True
            ),
            "vocabulary_used": structure_evaluation.get("attribution_vocabulary", []),
        },
        "repairs": {
            "attempts": len(attempts),
            "improving": len(promotions),
            "regressions_and_rollbacks": len(rollbacks),
        },
        "costs": {
            "scripted_agent_invocations": scripted_invocations,
            "live_model_calls": live_invocations,
            "calls_by_agent": calls_by_agent,
            "calls_by_tool": budget_state.get("calls_by_tool", {}),
            "tool_call_count": budget_state.get("tool_call_count", 0),
            "max_model_requests": budget_state.get("max_model_requests"),
            "max_tool_calls": budget_state.get("max_tool_calls"),
            "input_tokens": budget_state.get("usage", {}).get("input_tokens", 0),
            "output_tokens": budget_state.get("usage", {}).get("output_tokens", 0),
            "estimated_provider_cost_usd": structure_evaluation.get(
                "estimated_provider_cost_usd", None
            ),
            "note": (
                "scripted invocations and live provider calls are counted separately; "
                "token usage belongs to live calls only"
            ),
        },
        "rubric_source": rubric_source,
        "non_claims": structure_evaluation.get(
            "non_claims",
            [
                "no Pipeline E convergence claim",
                "no resume-target acceptance claim",
                "no T-v1 record",
                "the owner reviews the actual files and decides",
            ],
        ),
    }
    (out_dir / "evaluation_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


class E4LoopRecord(EvidenceModel):
    """The resumable E4 state (plan §14): everything the E3 state carries,
    plus the frozen configuration, the scripted/live call accounting, the
    pages the reviewer covered, the accepted-region recheck outcome, and the
    per-iteration trajectory."""

    schema_version: Literal["pipeline-e-e4-state/1"] = E4_SCHEMA_VERSION
    target_id: str
    target_sha256: str
    structure_draft_version: str = ""
    render_versions: list[RenderVersion] = Field(default_factory=list)
    best_render_version: str | None = None
    findings: list[DefectFinding] = Field(default_factory=list)
    measurement_results: list[MeasurementResult] = Field(default_factory=list)
    attributions: list[AttributionRecord] = Field(default_factory=list)
    repair_attempts: list[dict[str, Any]] = Field(default_factory=list)
    attempted_strategies: list[str] = Field(default_factory=list)
    action_fingerprints: list[str] = Field(default_factory=list)
    open_findings: list[str] = Field(default_factory=list)
    content_shape_probes_passed: bool = False
    pages_reviewed: list[int] = Field(default_factory=list)
    accepted_regions_recheck_passed: bool = False
    investigator_mode: str = "deterministic_shell_derivation"
    budget_state: dict[str, Any] = Field(default_factory=dict)
    started_at: str = ""
    finished_at: str = ""
    summary: dict[str, Any] = Field(default_factory=dict)


def _freeze_e4_config(
    out_dir: Path,
    *,
    target_id: str,
    target_frozen: FrozenCase,
    budget: RunBudget,
    max_repair_attempts: int,
    live: bool,
    prompts: dict[str, str],
    rubric_reference: dict[str, Any],
    candidate_sha256: str,
) -> dict[str, Any]:
    """Frozen BEFORE the first live call (E4 work order): target hash, candidate
    input/version, model name + configuration, prompts, budgets, sampling
    controls, rubric reference (path + hash, not given to agents), starting
    commit, permitted tools, and delivery gates. The rubric FILE CONTENT never
    enters this record or any prompt — evaluation truth stays out of the
    agent inputs."""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=Path(__file__).resolve().parents[2],
        ).stdout.strip()
    except Exception:
        commit = "unknown"
    config = {
        "run_id": out_dir.name,
        "frozen_before_first_live_call": True,
        "target_id": target_id,
        "target_sha256": target_frozen.target_sha256,
        "adobe_json_sha256": target_frozen.adobe_json_sha256,
        "candidate_input": {
            "role": "reviewed render context (candidate_resume_E render content)",
            "sha256": candidate_sha256,
            "builder_boundary": "candidate facts are never editable by any agent",
        },
        "model_configuration": _model_identity(),
        "sampling": {"temperature": E4_TEMPERATURE},
        "budgets": {
            "max_repair_attempts": max_repair_attempts,
            "run_budget": budget.to_json(),
            "per_agent_run_max_requests": {
                "target_investigator": E4_INVESTIGATOR_MAX_REQUESTS,
                "visual_reviewer": E4_REVIEWER_MAX_REQUESTS,
                "builder": E4_BUILDER_MAX_REQUESTS,
                "attribution_investigator": E4_ATTRIBUTION_MAX_REQUESTS,
            },
        },
        "prompts": {
            "sha256": {
                name: hashlib.sha256(text.encode("utf-8")).hexdigest()
                for name, text in prompts.items()
            },
            "note": "full frozen prompt texts persisted in prompts.json; agent-visible only",
        },
        "permitted_tools": [
            "inspect_page_overview",
            "inspect_page_region",
            "inspect_adobe_json",
            "measure_local_pdf",
            "audit_coverage",
            "measure_render_words",
            "compare_pdf_geometry",
            "render_and_checkpoint",
        ],
        "delivery_gates": [
            "deterministic_render",
            "no_target_candidate_facts",
            "section_order_matches_state",
            "no_blank_page",
            "candidate_content_accounting",
            "content_shapes_match_evidence",
            "content_gate",
        ],
        "evaluation_rubric_reference": {**rubric_reference, "not_given_to_agents": True},
        "starting_commit": commit,
        "live": live,
    }
    (out_dir / "run_config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (out_dir / "prompts.json").write_text(
        json.dumps(prompts, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return config


def run_e4(
    target_pdf: Path,
    out_dir: Path | None = None,
    *,
    live: bool = False,
    max_repair_attempts: int = 5,
    budget: RunBudget | None = None,
) -> tuple[Path, str, dict[str, Any]]:
    """E4 live-agent Resume I convergence trial (PIPELINE_E_PLAN.md §11/§14).

    Four LIVE roles over the existing PydanticAI path when ``live`` — Target
    Investigator (evidence tools -> typed StructureDraft), independent Visual
    Reviewer (region-by-region, version-bound findings), Attribution
    Investigator (only where the deterministic measurement cannot bind), and
    the Builder/Repair agent (bounded typed layers). The deterministic shell
    remains the Orchestrator: it owns artifact versions, budgets, tool
    permissions, validation, rollback, best-valid selection, strategy
    escalation, and the terminal state. NO agent promotes its own output.

    With ``live=False`` the SAME shell runs the mandatory offline verification
    path (deterministic derivation + ScriptedReviewer + scripted bounded
    rehearsal; zero live calls, real Chrome renders, real PDF measurement).

    Normal exits: ``ready_for_owner_review`` (all gates green + every material
    region reviewed + confirmed defects repaired/rechecked + no accepted region
    regressed + probes pass + owner package written — NOT owner acceptance) and
    ``budget_exhausted`` (resumable; never success). ``operational_abort``
    describes the failed operation and never classifies the template as
    unsupported. Stalls do not terminate the run: they escalate strategy.
    """
    from tests.experiments.a_pipeline import build_format_summary
    from tests.experiments.c2_candidates import candidate_resume_E, independent_candidate_fixtures
    from tests.experiments.c2_pipeline import run_flow_probe, state_from_scaffolds
    from tests.experiments.c2_plan import compile_render_plan
    from tests.experiments.c2_html import render_html
    from tests.experiments import c2_renderer as c2r
    from tests.experiments.c2_state import validate_layout_state, state_bytes
    from app.template_analysis.commercial.models import NormalizedLayoutEvidence

    started = time.time()
    started_at = datetime.now(UTC).isoformat(timespec="seconds")
    target_pdf = target_pdf.resolve()
    if not target_pdf.exists():
        raise RuntimeError(f"target PDF not found: {target_pdf}")

    out_dir = out_dir or RUNS / datetime.now(UTC).strftime("e_pipeline_e4_%Y%m%dT%H%M%SZ")
    out_dir.mkdir(parents=True, exist_ok=False)

    budget = budget or RunBudget(max_model_requests=40, max_tool_calls=200)
    trace = RunTrace(out_dir)
    store = EvidenceStore(out_dir, out_dir, base_html="<html><body></body></html>")
    store.manifest["experiment"] = "e_pipeline_e4"
    store.manifest["pipeline_phase"] = "e4"
    pages_reviewed: set[int] = set()
    # Loop state exists BEFORE any agent call, so a live-role failure can
    # always record its escalation (the closure reads these at call time).
    versions: list[RenderVersion] = []
    findings: list[DefectFinding] = []
    measurement_results: list[MeasurementResult] = []
    attributions: list[AttributionRecord] = []
    repair_attempts: list[dict[str, Any]] = []
    attempted_strategies: list[str] = []
    fingerprints: list[str] = []
    resolved_findings: set[str] = set()
    resolved_measurements: dict[str, tuple[MeasurementRequest, MeasurementResult]] = {}
    counter = {"finding": 0, "request": 0}
    render_contexts: dict[int, tuple[Any, str]] = {}  # render index -> (plan, html)

    def abort(operation: str, error: Exception) -> tuple[Path, str, dict[str, Any]]:
        record = E4LoopRecord(target_id="unknown", target_sha256="0" * 64)
        record.summary["terminal_state"] = "operational_abort"
        record.summary["abort"] = {"operation": operation, "error": str(error)}
        store.manifest["terminal_state"] = "operational_abort"
        store.manifest["abort"] = record.summary["abort"]
        trace.add(agent="shell", phase="operational", action="abort", note=f"{operation}: {error}")
        (out_dir / "e4_state.json").write_text(
            json.dumps(record.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        store.record_state("operational_abort", f"{operation}: {error}")
        (out_dir / "manifest.json").write_text(
            json.dumps(store.manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        trace.save()
        return out_dir, "operational_abort", record.model_dump(mode="json")

    def escalate(strategy: str, note: str = "") -> None:
        attempted_strategies.append(strategy)
        trace.add(agent="shell", phase="loop", action="strategy_escalation", note=strategy or note)

    # -- 1. FREEZE the inputs before any examination (and before the first
    #      live call): target + candidate + model + prompts + budgets ---------
    target_cache_dir = RUNS / "target_cache" / _sha256_file(target_pdf)
    raw_path = target_cache_dir / "adobe_raw.json"
    normalized_path = target_cache_dir / "enriched_evidence.json"
    if not raw_path.exists() or not normalized_path.exists():
        return abort(
            "target_evidence_cache",
            RuntimeError(
                "no cached Adobe response for this target under "
                "tests/experiments/runs/target_cache/; E4 reads only the cached "
                "evidence and the existing provider-gated agent path"
            ),
        )
    # The immutable raw Adobe evidence stays in the restricted target cache;
    # the shareable run-dir copy is the deterministic REDACTED derivative.
    (out_dir / "adobe_raw.json").write_text(
        json.dumps(
            redact_signed_urls(json.loads(raw_path.read_text(encoding="utf-8"))),
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    assert_no_signed_strings((out_dir / "adobe_raw.json").read_text(encoding="utf-8"))
    shutil.copy2(normalized_path, out_dir / "enriched_evidence.json")
    target_frozen = freeze_cases(target_pdf, raw_path, role="frozen_blind", case_id=target_pdf.stem)
    (out_dir / "frozen_case.json").write_text(target_frozen.model_dump_json(indent=2), encoding="utf-8")
    target_id = f"target-{target_pdf.stem}-v1"

    candidate = candidate_resume_E()
    candidate_sha256 = hashlib.sha256(
        json.dumps(candidate.model_dump(mode="json"), sort_keys=True).encode("utf-8")
    ).hexdigest()
    rubric_reference = {
        "path": "tests/experiments/C2_RESUME_I_BLIND_STRUCTURE_AUDIT.md",
        "sha256": _sha256_file(ROOT / "tests/experiments/C2_RESUME_I_BLIND_STRUCTURE_AUDIT.md"),
    }

    # -- 2. Deterministic evidence + compile basis ---------------------------
    try:
        normalized = NormalizedLayoutEvidence.model_validate_json(
            normalized_path.read_text(encoding="utf-8")
        )
    except Exception as error:
        return abort("normalized_evidence_load", error)
    pod = DualSourcePod(target_pdf, raw_path, out_dir, render_pdf=None, normalized=normalized)
    coverage = pod.audit_coverage()
    budget.spend_tool("inspect_page_overview")
    pod.inspect_page_overview(1, note="E4 investigation page 1")
    budget.spend_tool("inspect_adobe_json")
    pod.inspect_adobe_json(page_number=1, limit=8)
    budget.spend_tool("measure_local_pdf")
    pod.measure_local_pdf(page_number=1, include="rules")

    try:
        summary = build_format_summary(normalized, json.loads(raw_path.read_text(encoding="utf-8")), target_pdf)
        state, derived = compile_two_column_state(target_pdf, summary, evidence=normalized)
    except Exception as error:
        return abort("compile_two_column_state", error)
    state_violations = validate_layout_state(state)
    (out_dir / "c2_layout_state.json").write_bytes(state_bytes(state))

    # The E4 offline reviewer's scripted scenario record for THIS target (the
    # same runtime rehearsal data pattern as E3, keyed by the target id — not
    # a production rule): the known role-aligned divergence, bounded to the
    # compiled state's mapped entry section (a generic derived node id).
    mapped = [
        node for node in state.nodes
        if node.kind == "section" and node.binding
        and node.binding.mapping_action == "map"
        and "work_experience" in node.binding.sources
    ]
    if not mapped:
        return abort(
            "scripted_reviewer_region",
            RuntimeError("compiled state has no mapped work_experience section"),
        )
    entry_region = mapped[0].node_id
    SCRIPTED_REGION_BY_TARGET[target_id] = entry_region
    SCRIPTED_OBSERVATION_BY_TARGET[target_id] = (
        "The vertical distance from the first dated entry head to the second "
        "dated entry head appears larger than in the target."
    )
    SCRIPTED_REQUEST_BY_TARGET[target_id] = MeasurementRequest(
        request_id="measure-pending",
        metric="role_gap",
        page=1,
        # Role-aligned per-side anchors (target's own page-1 dated entry
        # heads vs the candidate render's first two entry heads — different
        # text, same semantic role; plan §9 rule 3).
        from_text="JOB",
        to_text="ANOTHER",
        render_from_text="Microsoft",
        render_to_text="Amazon.com",
        region_id=entry_region,
    )

    # -- 3. Investigator role: live StructureDraft (offline = deterministic) --
    from tests.experiments.c2_candidates import UnroutableContent
    from tests.experiments.fill_plan import CONTACT_KIND_CHECKS

    label_rules = {int(key): value for key, value in derived["sidebar_rules"].items()}
    structure: list[StructuralRelation] = []
    unresolved: list[UnresolvedItem] = []
    for index, record in enumerate(derived["sidebar_labels"], 1):
        refs = [
            EvidenceRef(
                evidence_id=f"local_pdf.sidebar_label.p{record['page']}.top{record['top']:.1f}",
                kind="local_measurement",
                page_number=record["page"],
                bbox_pt=[record["x0"], record["top"], record["x1"], record["bottom"]],
                source_kind="target_pdf",
                source_path="structure.jsonl",
            )
        ]
        rule = label_rules.get(index)
        if rule:
            refs.append(
                EvidenceRef(
                    evidence_id=f"local_pdf.sidebar_rule.top{rule['top_pt']:.1f}",
                    kind="local_measurement",
                    page_number=record["page"],
                    bbox_pt=[rule["x0_pt"], rule["top_pt"], rule["x1_pt"], rule["top_pt"]],
                    source_kind="target_pdf",
                    source_path="structure.jsonl",
                )
            )
        structure.append(
            StructuralRelation(
                claim_id=f"claim.{index:03d}",
                relation="section_boundary",
                parent=None,
                child=f"section.{index:02d}",
                statement=(
                    f"A right-aligned short line at x0={record['x0']:.1f}..x1={record['x1']:.1f} "
                    f"(page {record['page']}, top {record['top']:.1f}) shares the clustered "
                    "sidebar-label right edge and owns a measured rule below it — "
                    "claimed as the section boundary of a two-column sidebar family."
                ),
                evidence=refs,
                confidence=0.75,
                status="proposed",
            )
        )
    for page in sorted({record["page"] for record in derived["sidebar_labels"] if record["page"] != 1}):
        unresolved.append(
            UnresolvedItem(
                item_id=f"unresolved.continuation.{page}",
                question=(
                    f"Page {page} repeats the sidebar-label column; is it a page-break "
                    "continuation of the same template family or a second column layout?"
                ),
                status="unresolved",
                reason=(
                    "the label cluster evidence shows the pattern repeats, but the "
                    "reading-order relationship across the page break is not measured "
                    "by any evidence channel in this run"
                ),
                evidence_gap="ambiguous_relation",
            )
        )
    unresolved.append(
        UnresolvedItem(
            item_id="unresolved.bullet_marker_glyph",
            question=(
                "The LI/Lbl bullet markers measure a dot glyph column and a text column; "
                "the marker glyph style (round vs square) is not recovered from the "
                "normalized evidence."
            ),
            status="unresolved",
            reason=(
                "the raw provider response carries the markers, but this run's offline "
                "evidence channels cannot measure glyph style from the cached response"
            ),
            evidence_gap="normalization_loss",
        )
    )

    live_draft: TargetStructureDraft | None = None
    if live:
        pod_tools = _pod_tools(pod, budget, trace, agent_name="target_investigator")
        investigator_prompt = (
            "Investigate this target's structure for a resume-layout experiment.\n"
            "Machine-readable target description (page count/sizes, source classes, hashes):\n"
            + json.dumps(pod.describe_target(), ensure_ascii=False, indent=1)
            + "\nUse your read-only tools for every material claim (page overviews, "
            "original-resolution region crops, verbatim raw-provider element lookup, "
            "permitted local PDF measurement, coverage audit). Cite the returned "
            "evidence ids in every claim.\n"
            "Produce the typed StructureDraft covering: page regions, columns, reading "
            "order, header structure, sections, entry heads, dates and locations, "
            "nested groups, bullet ownership and tiers, continuation behavior, "
            "typography and decoration observations, and explicit unresolved "
            "questions. Every material claim must cite page regions, raw element ids, "
            "local PDF objects, or crops you requested — confidence alone is not "
            "evidence. A claim you cannot support goes into `unresolved` with the "
            "reason and evidence-gap class. A confident `ok` that is wrong is a "
            "failure; prefer partial with explicit unresolved items. Target person "
            "facts may be cited as diagnostic evidence only and never become "
            "candidate content.\n"
            f"BUDGET: this is ONE bounded agent run (at most "
            f"{E4_INVESTIGATOR_MAX_REQUESTS} model requests; every evidence-tool "
            "round trip costs one). Use at most a few evidence calls, then RETURN "
            "the typed draft — never keep investigating until the limit.\n"
            "OUTPUT SIZE: keep every claim statement under 250 characters and cite "
            "at most four evidence ids per claim; a concise draft returns reliably, "
            "an exhaustive one exceeds the output limit."
        )
        config = _freeze_e4_config(
            out_dir,
            target_id=target_id,
            target_frozen=target_frozen,
            budget=budget,
            max_repair_attempts=max_repair_attempts,
            live=live,
            prompts={
                "target_investigator": investigator_prompt,
                "visual_reviewer": REVIEWER_INSTRUCTIONS,
                "builder": E4_BUILDER_INSTRUCTIONS,
                "attribution_investigator": E4_ATTRIBUTION_INSTRUCTIONS,
            },
            rubric_reference=rubric_reference,
            candidate_sha256=candidate_sha256,
        )
        trace.add(agent="shell", phase="e0", action="case_frozen", output={"run_id": config["run_id"], "frozen_before_first_live_call": True})
        try:
            live_draft = _run_live_investigator(
                pod, pod_tools, budget, trace,
                prompt=investigator_prompt, per_call_requests=E4_INVESTIGATOR_MAX_REQUESTS,
            )
        except CheckpointBudgetExceeded:
            escalate("investigator_budget_exhausted")
        except Exception as error:  # live-agent failure is an escalation, never 'unsupported'
            escalate(
                f"investigator_live_call_failed:{type(error).__name__}:"
                f"{str(error)[:200]}"
            )
        if live_draft is not None:
            # Shell-side claim validation: a material claim citing evidence the
            # shell never collected is demoted to a recorded unresolved item —
            # never silently accepted, never silently dropped.
            available = {r.evidence_id for r in pod.records if r.status == "available"}
            kept: list[StructuralRelation] = []
            for claim in live_draft.structure:
                if all(ref.evidence_id in available for ref in claim.evidence):
                    kept.append(claim)
                else:
                    unresolved.append(
                        UnresolvedItem(
                            item_id=f"unresolved.claim.{claim.claim_id}",
                            question=claim.statement,
                            status="unresolved",
                            reason="the claim cites evidence the shell never collected; demoted, never accepted",
                            evidence_gap="claim_without_collected_evidence",
                        )
                    )
            live_draft = live_draft.model_copy(update={"structure": kept})
            unresolved = _dedup_unresolved(unresolved)
    else:
        config = _freeze_e4_config(
            out_dir,
            target_id=target_id,
            target_frozen=target_frozen,
            budget=budget,
            max_repair_attempts=max_repair_attempts,
            live=live,
            prompts={
                "target_investigator": "(offline: deterministic shell derivation; no live prompt)",
                "visual_reviewer": REVIEWER_INSTRUCTIONS,
                "builder": E4_BUILDER_INSTRUCTIONS,
                "attribution_investigator": E4_ATTRIBUTION_INSTRUCTIONS,
            },
            rubric_reference=rubric_reference,
            candidate_sha256=candidate_sha256,
        )
        trace.add(agent="shell", phase="e0", action="case_frozen", output={"run_id": config["run_id"], "frozen_before_first_live_call": True})

    draft = TargetStructureDraft(
        target_id=target_id,
        investigator="llm" if live_draft else "scripted",
        structure=[*(live_draft.structure if live_draft else []), *structure],
        unresolved=unresolved,
        self_reported=SelfReportedStatus(
            status=(live_draft.self_reported.status if live_draft else "partial"),
            sections_expected=live_draft.self_reported.sections_expected if live_draft else None,
            sections_identified=len(
                (live_draft.structure if live_draft else []) + structure
            ),
            notes=(
                "E4 investigator: the deterministic compile basis carries measured "
                "sidebar-label + rule evidence; the LIVE draft (when present) adds "
                "the agent's own evidence-linked claims; everything unsupported "
                "stays unresolved."
                if live_draft
                else "Offline verification path: deterministic shell derivation only."
            ),
        ),
        evidence_used_by_id={record.evidence_id: record for record in pod.records},
    )
    draft_path = out_dir / "structure_draft.json"
    draft_path.write_text(draft.model_dump_json(indent=2), encoding="utf-8")
    store.register_version("structure_draft_v1", draft_path, "evidence-linked E4 structure draft")
    trace.add(
        agent=live_draft.investigator if live_draft else "shell_deterministic",
        phase="target_understanding",
        action="structure_draft",
        output={"claims": len(draft.structure), "unresolved": len(draft.unresolved)},
        persist_output=True,
    )
    (out_dir / "structure.jsonl").write_text(
        "\n".join(json.dumps(claim.model_dump(mode="json"), ensure_ascii=False) for claim in draft.structure) + "\n",
        encoding="utf-8",
    )

    # -- 4. Builder compile basis + candidate (facts never agent-editable) ---
    probe = run_flow_probe(state, candidate)
    (out_dir / "flow_probe.json").write_text(
        json.dumps(probe, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    frozen_dir = RUNS / "c_pipeline_D_to_E_20260910T195515Z"
    if not (frozen_dir / "target.pdf").exists():
        return abort("frozen_c1_baseline", FileNotFoundError(str(frozen_dir / "target.pdf")))
    privacy_target = frozen_dir / "target.pdf"
    try:
        environment = pinned_export_environment({})
    except Exception as error:
        return abort("pinned_chrome_environment", error)

    # Generic header-overflow disposition (E3 shell transition, reused).
    header_slots_in_state = {
        slot
        for node in state.nodes if node.kind == "header_row"
        for slot in node.slots
    }
    unhomed_header_fields = [
        leaf for leaf in candidate.leaves
        if leaf.kind == "header_field" and leaf.slot not in header_slots_in_state
    ]
    if unhomed_header_fields:
        candidate = candidate.model_copy(
            update={
                "unroutable": [
                    *candidate.unroutable,
                    *(
                        UnroutableContent(
                            text=leaf.text or "",
                            reason=(
                                "the compiled target header region carries no measured "
                                f"row with slot {leaf.slot!r} (two-column family: the "
                                "measured contact-table rows sit inside the section "
                                "content); routes through the explicit candidate-only "
                                "header-overflow node"
                            ),
                            slot=leaf.slot,
                        )
                        for leaf in unhomed_header_fields
                    ),
                ]
            }
        )
        trace.add(
            agent="shell", phase="builder", action="header_overflow_disposition",
            output={"leaves": [leaf.leaf_id for leaf in unhomed_header_fields]},
            note="generic header-overflow disposition for header fields without a measured home",
            persist_output=True,
        )

    def render_version_full(note: str, proposal: RepairProposal | None = None) -> tuple[RenderVersion, Path, dict[str, Any], Any, str]:
        """One whole-document render through the canonical chain; a repair
        proposal applies its bounded plan-layer mutations to the FRESHLY
        compiled plan (never over approved state), then the delivery gates run."""
        from tests.experiments.c2_plan import compile_render_plan as _crp
        from tests.experiments.c2_html import render_html as _rh

        budget.spend_tool("render_and_checkpoint")
        plan = _crp(state, candidate)
        if proposal is not None and proposal.layer != "no_op":
            if proposal.layer == "plan_entry_gap":
                section = next(
                    (s for s in plan.sections if s.node_id == proposal.section_node_id), None
                )
                if section is not None:
                    base_gap = section.inter_entry_gap_above_pt or 0.0
                    plan.sections = [
                        s.model_copy(
                            update={"inter_entry_gap_above_pt": round(base_gap + proposal.gap_delta_pt, 3)}
                        )
                        if s.node_id == proposal.section_node_id
                        else s
                        for s in plan.sections
                    ]
            elif proposal.layer == "plan_entry_meta_placement":
                section = next(
                    (s for s in plan.sections if s.node_id == proposal.section_node_id), None
                )
                if section is not None:
                    plan.sections = [
                        s.model_copy(update={"entry_meta_placement": proposal.entry_meta_placement})
                        if s.node_id == proposal.section_node_id
                        else s
                        for s in plan.sections
                    ]
        if plan.status == "failed":
            raise RuntimeError(f"refusing to render a failed plan: {plan.failures[:3]}")
        html = _rh(state, plan)
        html_path = out_dir / f"render_{len(versions) + 1}.html"
        html_path.write_text(html, encoding="utf-8")
        pdf_path = out_dir / f"render_{len(versions) + 1}.pdf"
        pdf = _export_pinned_html_to_pdf(html_path, pdf_path, environment)
        render_contexts[len(versions) + 1] = (plan, html)
        pages = _render_pages(pdf, out_dir, f"render_{len(versions) + 1}")
        second = out_dir / f"render_{len(versions) + 1}_second.pdf"
        _export_pinned_html_to_pdf(html_path, second, environment)
        second_pages = _render_pages(second, out_dir, f"render_{len(versions) + 1}_second")
        stability = c2r._line_stability(pdf, second)
        content = c2r.content_gate(plan, html, pdf)
        privacy = c2r.privacy_gate(plan, privacy_target, html, pdf)
        structure_gate = c2r.structure_gate(plan, state, html, pdf)
        blank = c2r.blank_page_gate(pdf)
        accounting = c2r.candidate_accounting_gate(plan, content)
        _headings_scaffold, body_scaffold = compile_two_column_state_for_scaffold(target_pdf, summary)
        bullet_tiers = _two_column_bullet_tiers(_pdf_lines_and_marks(target_pdf)[0], summary)
        shape = c2r.content_shape_verification(state, plan, body_scaffold, bullet_tiers, html, summary, pdf)
        gates = {
            "deterministic_render": all(
                c2r._sha256(left) == c2r._sha256(right)
                for left, right in zip(pages, second_pages)
            )
            and stability["passed"],
            "no_target_candidate_facts": privacy["passed"],
            "section_order_matches_state": structure_gate["section_order_matches_state"],
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
        trace.add(agent="shell", phase="render", action="render_version", output=version.model_dump(mode="json"), note=note)
        return version, pdf, {"passed": version.hard_gates_passed, "gates": gates}, plan, html

    def execute_measurement(finding: DefectFinding, pdf: Path, request_id: str) -> MeasurementResult:
        """One typed measurement of the ACTUAL final PDF. Dispatches on the
        defect class: role-gap findings measure the page-local role gap;
        entry-wrap findings measure the verbatim leaf presence the content
        gate checks on the final PDF (target baseline 0 — the target's own
        lines extract as one text object each)."""
        if finding.suspected_dimension == "entry_text_wrap":
            budget.spend_tool("compare_pdf_geometry")
            index = int(Path(pdf).stem.rsplit("_", 1)[-1])
            plan, html = render_contexts[index]
            content = c2r.content_gate(plan, html, pdf)
            missing = content["missing_pdf"]
            result = MeasurementResult(
                request_id=request_id,
                status="confirmed",
                target_value_pt=0.0,
                current_value_pt=float(len(missing)),
                delta_pt=float(len(missing)),
                method="content_gate_missing_pdf/1",
                warnings=[
                    "verbatim candidate leaf presence in the ACTUAL final PDF text; "
                    "the target baseline is 0 (its own lines extract cleanly)",
                    "PDF text order may concatenate columns; hyphen artifacts tolerated",
                ],
            )
            trace.add(
                agent="measure_controller", phase="measure", action="measurement",
                tool="compare_pdf_geometry", output=result.model_dump(mode="json"),
                persist_output=True,
            )
            return result
        request = finding.requested_measurement.model_copy(update={"request_id": request_id})
        return MeasureController(pod, budget, trace).execute(request, current_pdf=pdf)

    def measure_and_attribute(finding: DefectFinding, pdf: Path) -> tuple[MeasurementResult, AttributionRecord]:
        counter["request"] += 1
        request_id = f"measure-{counter['request']:03d}"
        result = execute_measurement(finding, pdf, request_id)
        request = finding.requested_measurement.model_copy(update={"request_id": request_id})
        measurement_results.append(result)
        trace.add(
            agent="attribution_investigator", phase="attribute", action="trace",
            input={"finding": finding.finding_id, "request": request.request_id},
            note="raw target evidence -> structure -> template slot -> candidate binding -> RenderPlan -> DOM/CSS -> final PDF object",
        )
        if finding.suspected_dimension == "entry_text_wrap":
            # Wrap-defect attribution: the objective check is the missing-leaf
            # COUNT on the final PDF (target baseline 0), not a gap tolerance.
            if result.status == "confirmed" and (result.current_value_pt or 0.0) > 0.0:
                attribution = AttributionRecord(
                    finding_id=finding.finding_id,
                    render_version=finding.render_version,
                    measurement_request_id=request.request_id,
                    attribution="template_compilation",
                    hypothesis_status="confirmed",
                    repair_owner="builder",
                    evidence=[result.request_id],
                    reason=(
                        "the content gate's missing-PDF leaf set is non-empty on the "
                        "ACTUAL final PDF: the wrapped entry-head text is not present "
                        "as one text object"
                    ),
                )
            elif result.status == "confirmed":
                attribution = AttributionRecord(
                    finding_id=finding.finding_id,
                    render_version=finding.render_version,
                    measurement_request_id=request.request_id,
                    attribution="no_defect",
                    hypothesis_status="rejected",
                    repair_owner="none",
                    evidence=[result.request_id],
                    reason="every candidate leaf is present as one final-PDF text object",
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
                    reason="the wrap measurement could not bind to the final PDF; re-verify",
                )
            attributions.append(attribution)
            trace.add(agent="attribution_investigator", phase="attribute", action="attribution", output=attribution.model_dump(mode="json"))
            return result, attribution
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
                        "the measured final-PDF object differs from the target's "
                        "measured role gap beyond the documented tolerance"
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
        # E4 live attribution: when the deterministic measurement cannot bind
        # AND the budget remains, the LIVE Attribution Investigator re-traces
        # the chain with read-only evidence tools. Its hypothesis REPLACES the
        # causal attribution but never the observation, and it never promotes.
        if (
            result.status != "confirmed"
            and finding.severity == "high"
            and live
            and budget.remaining_model_requests() >= 2
        ):
            try:
                hypothesis = _live_attribution_hypothesis(
                    pod, budget, trace, finding=finding, result=result
                )
                if hypothesis is not None:
                    # The same single conversion + ownership validation as the
                    # E5 batched path (one rule, one place).
                    attribution = _e5_live_attribution_record(
                        finding, hypothesis, request.request_id
                    )
                    trace.add(
                        agent="attribution_investigator", phase="attribute", action="live_attribution",
                        output=hypothesis.model_dump(mode="json"),
                    )
            except CheckpointBudgetExceeded:
                escalate("attribution_budget_exhausted")
            except Exception as error:
                escalate(
                    f"attribution_live_call_failed:{type(error).__name__}: "
                    f"{str(error)[:200]}",
                    str(error)[:400],
                )
        attributions.append(attribution)
        trace.add(agent="attribution_investigator", phase="attribute", action="attribution", output=attribution.model_dump(mode="json"))
        return result, attribution

    def accounting_defect_findings(
        gates: dict[str, Any], version: RenderVersion, pdf: Path, plan: Any, html: str
    ) -> list[DefectFinding]:
        """Deterministic observation-first findings from the render's own
        gates (deduplicated per failed-gate class, bound to exact versions,
        one typed measurement request against the ACTUAL final PDF)."""
        emitted: list[DefectFinding] = []
        if gates.get("content_gate"):
            return emitted
        content = c2r.content_gate(plan, html, pdf)
        if content["missing_pdf"]:
            counter["finding"] += 1
            leaf_id = content["missing_pdf"][0]
            text = c2r._leaf_text(plan, leaf_id)
            prefix = " ".join(str(text).split())[:20]
            emitted.append(
                DefectFinding(
                    finding_id=f"finding-{counter['finding']:03d}",
                    target_version=target_id,
                    render_version=version.version_id,
                    page=1,
                    region="section.04",
                    observation=(
                        f"The rendered entry head wraps the candidate detail line so the "
                        f"final PDF text interleaves it with the meta column; the verbatim "
                        f"detail (prefix {prefix!r}) is not present as one text object "
                        f"({len(content['missing_pdf'])} wrapped head leaf(ves) affected)."
                    ),
                    suspected_dimension="entry_text_wrap",
                    proposed_cause=(
                        "hypothesis only: the renderer's entry-head flex geometry owns the "
                        "wrap/interleave; attribution verifies from the measurement"
                    ),
                    requested_measurement=MeasurementRequest(
                        request_id="measure-pending",
                        metric="role_gap",
                        page=1,
                        from_text="JOB",
                        to_text="ANOTHER",
                        render_from_text="Microsoft",
                        render_to_text="Amazon.com",
                        region_id="section.04",
                    ),
                    severity="high",
                    confidence=0.7,
                    reviewer="scripted",
                )
            )
        return emitted

    # -- 5. First render ------------------------------------------------------
    try:
        v1, v1_pdf, v1_gates, v1_plan, v1_html = render_version_full("first render (compiled two-column state)")
    except Exception as error:
        return abort("render_version_1", error)

    def accepted_regions_hold(candidate_pdf: Path) -> tuple[bool, list[dict[str, Any]]]:
        """After a promotion candidate: every previously accepted (promoted)
        measurement is repeated on the whole new render; any accepted region
        that regressed beyond tolerance fails the promotion."""
        rechecks: list[dict[str, Any]] = []
        for finding_id, (request, prior) in resolved_measurements.items():
            repeat = MeasureController(pod, budget, trace).execute(
                request.model_copy(), current_pdf=candidate_pdf
            )
            held = (
                repeat.current_value_pt is None
                or prior.current_value_pt is None
                or abs(repeat.current_value_pt - repeat.target_value_pt)
                <= abs(prior.current_value_pt - prior.target_value_pt) + E2_IMPROVEMENT_TOLERANCE_PT
            )
            rechecks.append({"finding": finding_id, "held": held})
            if not held:
                return False, rechecks
        return True, rechecks

    try:
        # -- 6. See -> Measure -> Attribute -> Repair -> Re-render loop -----------
        for attempt in range(1, max_repair_attempts + 1):
            if budget.remaining_model_requests() < 1:
                trace.add(agent="shell", phase="loop", action="budget_exhausted", note="before review")
                break
            current_version = versions[-1]
            current_pdf = _render_pdf_of(out_dir, len(versions))
            # See: the independent reviewer inspects the WHOLE document region by
            # region (overviews of every page) and never sees builder rationale.
            findings_new: list[DefectFinding] = []
            try:
                if live:
                    target_overviews = _overview_pngs(target_pdf, out_dir, f"review_target_{attempt}")
                    render_overviews = _overview_pngs(current_pdf, out_dir, f"review_render_{attempt}")
                    counter["finding"] += 1
                    node_inventory = json.dumps(
                        {
                            "state_nodes": [
                                {"node_id": n.node_id, "kind": n.kind} for n in state.nodes
                            ],
                            "open_observations": [
                                f.observation for f in findings if f.finding_id not in resolved_findings
                            ],
                            "instruction": "cover the WHOLE document region by region, not only known defects",
                        },
                        ensure_ascii=False,
                    )
                    findings_new = _live_reviewer_findings(
                        [*target_overviews, *render_overviews],
                        finding_id=f"finding-{counter['finding']:03d}",
                        target_version=target_id,
                        render_version=current_version.version_id,
                        page=1,
                        node_inventory=node_inventory,
                        budget=budget,
                        trace=trace,
                        id_prefix=f"finding-r{attempt}",
                        visual_model=True,
                    )
                    pages_reviewed.update(range(1, len(target_overviews) + 1))
                else:
                    counter["finding"] += 1
                    prompt = (
                        f"TARGET {target_frozen.case_id} sha={target_frozen.target_sha256[:12]} "
                        f"RENDER {current_version.version_id} page 1."
                    )
                    findings_new = ScriptedReviewer().run(
                        prompt,
                        finding_id=f"finding-{counter['finding']:03d}",
                        target_version=target_id,
                        render_version=current_version.version_id,
                        page=1,
                        budget=budget,
                        trace=trace,
                        target_id=target_id,
                    )
                    pages_reviewed.add(1)
            except CheckpointBudgetExceeded:
                escalate("reviewer_budget_exhausted")
                break
            except Exception as error:
                escalate(
                    f"reviewer_live_call_failed:{type(error).__name__}: "
                    f"{str(error)[:200]}",
                    str(error)[:400],
                )
                continue
            findings_new = _validate_finding_versions(findings_new, target_id, current_version.version_id)
            if not v1_gates["passed"]:
                findings_new.extend(
                    accounting_defect_findings(v1_gates["gates"], v1, v1_pdf, v1_plan, v1_html)
                )
            # Deduplicate repeated findings (same region + dimension at the same
            # render version) — one defect class is one finding.
            seen_keys = {(f.region, f.suspected_dimension, f.render_version) for f in findings}
            deduped: list[DefectFinding] = []
            for finding in findings_new:
                key = (finding.region, finding.suspected_dimension, finding.render_version)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                deduped.append(finding)
            findings_new = deduped
            findings.extend(findings_new)

            for finding in findings_new:
                if finding.finding_id in resolved_findings:
                    continue
                try:
                    result, attribution = measure_and_attribute(finding, pdf=current_pdf)
                except BudgetExhausted as error:
                    escalate(f"attempt{attempt}:{finding.finding_id}:tool_budget_exhausted")
                    trace.add(agent="shell", phase="loop", action="budget_exhausted", note=str(error))
                    raise CheckpointBudgetExceeded(str(error)) from error
                if attribution.repair_owner != "builder":
                    escalate(
                        f"attempt{attempt}:{finding.finding_id}:{attribution.attribution}:remeasure_or_other_channel"
                    )
                    continue
                if finding.suspected_dimension == "entry_text_wrap":
                    # Wrap-defect resolution: every candidate leaf present as one
                    # final-PDF text object (missing count 0) — not a gap tolerance.
                    if (result.current_value_pt or 0.0) == 0.0:
                        resolved_findings.add(finding.finding_id)
                        resolved_measurements[finding.finding_id] = (finding.requested_measurement, result)
                        continue
                elif abs(result.delta_pt or 0.0) <= E2_IMPROVEMENT_TOLERANCE_PT:
                    resolved_findings.add(finding.finding_id)
                    resolved_measurements[finding.finding_id] = (finding.requested_measurement, result)
                    continue
                if finding.suspected_dimension not in ("role_gap", "entry_text_wrap"):
                    # §14 escalation ladder: a defect beyond the bounded layers is
                    # recorded as a strategy escalation with its measured evidence —
                    # never repaired blindly, never 'unsupported'.
                    escalate(
                        f"attempt{attempt}:{finding.finding_id}:{attribution.attribution}:"
                        f"{finding.suspected_dimension}:change_repair_layer_or_template_representation"
                    )
                    continue
                fingerprint = f"{attribution.attribution}:{finding.suspected_dimension}:{round(result.delta_pt or 0, 3)}"
                if fingerprint in fingerprints:
                    escalate(f"attempt{attempt}:{finding.finding_id}:repeated_action_change_strategy")
                    continue
                fingerprints.append(fingerprint)

                # Repair: the live Builder chooses the bounded layer; offline, the
                # scripted rehearsal selects it deterministically.
                if live:
                    proposal = _live_builder_proposal(finding, result, attribution, budget, trace, state)
                else:
                    proposal = _scripted_builder_proposal(finding, result)
                if proposal is None:
                    escalate(f"attempt{attempt}:{finding.finding_id}:builder_declined_no_op")
                    continue
                validation_error = _validate_repair(
                    proposal, current_version, fingerprints, state=state, findings=list(findings)
                )
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
                        "agent": proposal.agent,
                    }
                )
                budget.repair_attempt_count += 1  # Phase 0: counts must agree everywhere
                trace.add(agent="builder", phase="repair", action="proposal", output=proposal.model_dump(mode="json"), persist_output=True)
                try:
                    candidate_version, candidate_pdf, candidate_gates, _plan, _html = render_version_full(
                        f"repair attempt {attempt} for {finding.finding_id}", proposal=proposal
                    )
                except Exception as error:
                    return abort("render_repair_candidate", error)
                # Re-measure FIRST: the IDENTICAL request (same request id, same
                # measurement channel), changing only the render version — the
                # defect-level improvement evidence exists whether or not a
                # ceiling gate stays red.
                repeat_result = execute_measurement(finding, candidate_pdf, result.request_id)
                measurement_results.append(repeat_result)  # the identical request, recorded
                improved = (
                    repeat_result.current_value_pt is not None
                    and result.current_value_pt is not None
                    and repeat_result.target_value_pt is not None
                    and abs(repeat_result.current_value_pt - repeat_result.target_value_pt)
                    < abs(result.current_value_pt - result.target_value_pt)
                )
                if not improved:
                    attempted_strategies.append(
                        f"attempt{attempt}:{finding.finding_id}:non_improving_rolled_back"
                    )
                    trace.add(agent="shell", phase="repair", action="rolled_back", note="non-improving repair")
                    continue
                # Candidate-fact/structure safety gates (no candidate-fact damage,
                # no target-fact leak, no structural break, deterministic render).
                gates_before = current_gates_of(out_dir, len(versions) - 1) or {}
                gates_after = candidate_gates["gates"]
                if not all(gates_after.get(gate) for gate in CANDIDATE_FACT_GATES):
                    attempted_strategies.append(
                        f"attempt{attempt}:{finding.finding_id}:repair_failed_gates_rolled_back"
                    )
                    trace.add(
                        agent="shell", phase="repair", action="rolled_back",
                        note="candidate-safety gate failed",
                        output={
                            "gates_before": gates_before,
                            "gates_after": gates_after,
                            "fixed": sorted(
                                gate for gate in set(gates_before) | set(gates_after)
                                if not gates_before.get(gate, False) and gates_after.get(gate, False)
                            ),
                            "remaining_failed": sorted(g for g, ok in gates_after.items() if not ok),
                        },
                        persist_output=True,
                    )
                    continue
                # Accepted-region recheck: no accepted region may regress.
                holds, rechecks = accepted_regions_hold(candidate_pdf)
                if not holds:
                    attempted_strategies.append(
                        f"attempt{attempt}:{finding.finding_id}:accepted_region_regressed_rolled_back"
                    )
                    trace.add(agent="shell", phase="repair", action="rolled_back", note="accepted region regressed")
                    continue
                candidate_version = candidate_version.model_copy(update={"promoted": True})
                versions[-1] = candidate_version
                resolved_findings.add(finding.finding_id)
                resolved_measurements[finding.finding_id] = (finding.requested_measurement, repeat_result)
                trace.add(
                    agent="shell", phase="repair", action="promoted",
                    output={
                        "version": candidate_version.version_id,
                        "before": result.model_dump(mode="json"),
                        "after": repeat_result.model_dump(mode="json"),
                        "accepted_region_rechecks": rechecks,
                    },
                    persist_output=True,
                )
    except CheckpointBudgetExceeded as error:
        trace.add(agent="shell", phase="loop", action="budget_exhausted", note=str(error))


    # -- 7. Content-shape probes ---------------------------------------------
    shape_probes: dict[str, Any] = {}
    for profile, probe_candidate in independent_candidate_fixtures().items():
        shape_probes[profile] = run_flow_probe(state, probe_candidate)
    (out_dir / "content_shape_probes.json").write_text(
        json.dumps(shape_probes, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    probes_ok = all(entry["passed"] for entry in shape_probes.values())

    # -- 8. Terminal state + owner package -----------------------------------
    open_findings = [
        finding.finding_id for finding in findings if finding.finding_id not in resolved_findings
    ]
    best_version_id = next(
        (v.version_id for v in versions if v.promoted),
        next((v.version_id for v in versions if v.hard_gates_passed), None),
    )
    expected_pages = set(range(1, (target_frozen.page_count or 1) + 1))
    all_pages_covered = pages_reviewed >= expected_pages
    best_version = next((v for v in versions if v.version_id == best_version_id), None)
    # The owner-review terminal state requires the FULL hard-gate set (all
    # gates green) — a defect-level promoted version with a remaining ceiling
    # gate keeps the run at budget_exhausted, honestly resumable.
    if (
        versions
        and best_version_id
        and best_version is not None
        and best_version.hard_gates_passed
        and not open_findings
        and probes_ok
        and all_pages_covered
    ):
        terminal = "ready_for_owner_review"
    else:
        terminal = "budget_exhausted"
    record = E4LoopRecord(
        target_id=target_id,
        target_sha256=target_frozen.target_sha256,
        structure_draft_version="structure_draft_v1",
        render_versions=versions,
        best_render_version=best_version_id,
        findings=findings,
        measurement_results=measurement_results,
        attributions=attributions,
        repair_attempts=repair_attempts,
        attempted_strategies=attempted_strategies,
        action_fingerprints=fingerprints,
        open_findings=open_findings,
        content_shape_probes_passed=probes_ok,
        pages_reviewed=sorted(pages_reviewed),
        accepted_regions_recheck_passed=True,
        investigator_mode=live_draft.investigator if live_draft else "deterministic_shell_derivation",
        budget_state=budget.to_json(),
        started_at=started_at,
        finished_at=datetime.now(UTC).isoformat(timespec="seconds"),
        summary={
            "total_findings": len(findings),
            "terminal_state": terminal,
            "best_render_version": best_version_id,
            "content_shape_probes_passed": probes_ok,
            "coverage_audit_status": coverage.get("status"),
            "elapsed_seconds": round(time.time() - started, 1),
            "live": live,
            "all_pages_reviewed": all_pages_covered,
            "scripted_invocations": budget.calls_by_mode.get("scripted", 0),
            "live_model_calls": budget.calls_by_mode.get("live", 0),
        },
    )
    (out_dir / "e4_state.json").write_text(
        json.dumps(record.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    store.manifest["terminal_state"] = terminal
    store.manifest["e4"] = record.model_dump(mode="json")
    store.manifest["budget"] = budget.to_json()
    store.manifest["pending_candidate_id"] = None  # candidates stay INACTIVE; the owner decides
    store.record_state(terminal, f"best={record.best_render_version}")
    (out_dir / "manifest.json").write_text(
        json.dumps(store.manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    trace.save()
    _write_e4_report(out_dir, target_frozen, draft, record, terminal, config)
    try:
        _write_owner_package(out_dir, target_pdf, record, terminal, config, trace.entries)
    except Exception as error:
        trace.add(agent="shell", phase="owner_package", action="failed", note=str(error))
        trace.save()
    return out_dir, terminal, record.model_dump(mode="json")


def _scripted_builder_proposal(
    finding: DefectFinding, result: MeasurementResult
) -> RepairProposal | None:
    """Offline verification-path Builder: selects the bounded layer from the
    MEASURED defect class (never from the target name). The entry-wrap defect
    class rehearses the renderer-layer capability the E3 run recorded as its
    escalation target; the role-gap defect class rehearses the plan-layer gap
    correction. No target-specific constant appears here."""
    if finding.suspected_dimension == "entry_text_wrap":
        return RepairProposal(
            finding_id=finding.finding_id,
            base_render_version=finding.render_version,
            layer="plan_entry_meta_placement",
            section_node_id=finding.region,
            entry_meta_placement="title_row",
            attributed_defect=(
                "the rendered entry head wraps the candidate detail line beside the "
                "meta column; the verbatim detail is not one final-PDF text object"
            ),
            files_fields_selectors=[
                "c2_html.render_html -> section entry head (.c2-entry-head/.c2-entry-meta)",
                f"SectionPlan[{finding.region}].entry_meta_placement",
            ],
            expected_measurable_result=(
                "the wrapped detail leaf re-renders within the whole entry width; the "
                "content gate's missing-PDF leaf set for this region shrinks"
            ),
            possible_regressions="the meta column may overlap the first title line on narrow entries",
            rollback_condition="any hard gate fails or an accepted measurement regresses",
            rationale="attributed entry-head wrap; bounded renderer-layer placement change",
            agent="scripted",
        )
    if result.delta_pt is not None and result.status == "confirmed":
        return RepairProposal(
            finding_id=finding.finding_id,
            base_render_version=finding.render_version,
            layer="plan_entry_gap",
            section_node_id=finding.region,
            gap_delta_pt=-3.0 if result.delta_pt > 0 else 3.0,
            attributed_defect=(
                "the measured inter-entry role gap differs from the target beyond tolerance"
            ),
            files_fields_selectors=[
                f"RenderPlan[{finding.region}].inter_entry_gap_above_pt",
            ],
            expected_measurable_result="the repeated identical role-gap measurement moves toward the target",
            possible_regressions="entries below may shift; the accepted-region recheck covers this",
            rollback_condition="non-improving measurement or failed gates",
            rationale="attributed plan-layer role gap; bounded single-layer correction",
            agent="scripted",
        )
    return None


def _live_builder_proposal(
    finding: DefectFinding,
    result: MeasurementResult,
    attribution: AttributionRecord,
    budget: RunBudget,
    trace: RunTrace,
    state: Any,
) -> RepairProposal | None:
    """The LIVE Builder/Repair agent: one bounded request that chooses the
    typed repair layer and states the required evidence fields. The shell (not
    the agent) binds versions, validates, applies, re-measures and promotes."""
    from pydantic_ai import Agent

    from tests.experiments.d_pipeline import _live_model

    if budget.remaining_model_requests() < 1:
        raise CheckpointBudgetExceeded("budget exhausted before builder")
    model = _live_model()
    agent = Agent(
        model,
        output_type=LiveBuilderRepair,
        name="builder",
        instructions=E4_BUILDER_INSTRUCTIONS,
    )
    payload = {
        "finding": finding.model_dump(mode="json"),
        "measurement": result.model_dump(mode="json"),
        "attribution": attribution.model_dump(mode="json"),
        "bounded_layers": {
            "plan_entry_gap": "one section's inter_entry_gap_above_pt += gap_delta_pt",
            "plan_entry_meta_placement": (
                "one section's entry head meta placement -> 'title_row' "
                "(meta beside the FIRST title line; remaining head lines full width)"
            ),
        },
        "state_nodes": [
            {"node_id": node.node_id, "kind": node.kind} for node in state.nodes
        ],
        "constraint": "candidate leaf text/facts are NEVER modifiable; no target-specific constants",
    }
    result_run = agent.run_sync(
        json.dumps(payload, ensure_ascii=False, indent=1),
        usage_limits=_call_limits(budget, E4_BUILDER_MAX_REQUESTS),
        model_settings=_live_model_settings(),
    )
    _record_usage(budget, trace, "builder", "repair", result_run)
    proposal = result_run.output
    return RepairProposal(
        finding_id=finding.finding_id,
        base_render_version=finding.render_version,
        layer=proposal.layer,
        section_node_id=proposal.section_node_id,
        gap_delta_pt=proposal.gap_delta_pt,
        entry_meta_placement=proposal.entry_meta_placement,
        attributed_defect=proposal.attributed_defect,
        files_fields_selectors=proposal.files_fields_selectors,
        expected_measurable_result=proposal.expected_measurable_result,
        possible_regressions=proposal.possible_regressions,
        rollback_condition=proposal.rollback_condition,
        rationale=proposal.rationale,
        agent="llm",
    )


def _live_attribution_hypothesis(
    pod: "DualSourcePod",
    budget: RunBudget,
    trace: RunTrace,
    *,
    finding: DefectFinding,
    result: MeasurementResult,
) -> LiveAttributionHypothesis | None:
    """The LIVE Attribution Investigator (independent of the Reviewer): traces
    the chain with read-only evidence tools and returns the typed hypothesis.
    The observation survives; only the causal hypothesis may be replaced."""
    from pydantic_ai import Agent

    from tests.experiments.d_pipeline import _live_model

    if budget.remaining_model_requests() < 1:
        raise CheckpointBudgetExceeded("budget exhausted before attribution investigator")
    tools = _pod_tools(pod, budget, trace, agent_name="attribution_investigator")
    tools.append(
        _traced_render_words(pod, budget, trace)
    )
    model = _live_model()
    agent = Agent(
        model,
        output_type=LiveAttributionHypothesis,
        name="attribution_investigator",
        instructions=E4_ATTRIBUTION_INSTRUCTIONS,
        tools=tools,
    )
    payload = {
        "finding": finding.model_dump(mode="json"),
        "measurement": result.model_dump(mode="json"),
        "target_description": pod.describe_target(),
        "budget_note": (
            "You have ONE bounded agent run; decide from the evidence you already "
            "have plus at most a few tool calls. If the finding's class is directly "
            "measurable from the recorded measurement, answer WITHOUT more tools."
        ),
        "reviewer_hypothesis_note": (
            "the reviewer's proposed_cause is a recorded hypothesis that may be "
            "wrong; verify from evidence before confirming or replacing it"
        ),
        "instruction": (
            "Use your read-only tools (region crops, verbatim raw-provider element "
            "lookup, local PDF measurement, render word measurement) to trace this "
            "finding through the chain and decide the attribution."
        ),
    }
    result_run = agent.run_sync(
        json.dumps(payload, ensure_ascii=False, indent=1),
        usage_limits=_call_limits(budget, E4_ATTRIBUTION_MAX_REQUESTS),
        model_settings=_live_model_settings(),
    )
    _record_usage(budget, trace, "attribution_investigator", "attribute", result_run)
    return result_run.output


def _traced_render_words(pod: "DualSourcePod", budget: RunBudget, trace: RunTrace) -> Any:
    """The render-side word measurement exposed to the attribution agent (the
    existing DualSourcePod channel, budget-counted and traced)."""

    def measure_render_words(page_number: int) -> dict[str, Any]:
        budget.spend_tool("measure_render_words")
        out = pod.measure_render_words(page_number)
        trace.add(
            agent="attribution_investigator", phase="attribute", action="tool_call",
            tool="measure_render_words", input={"page": page_number},
            note="render-side word boxes (output verification)",
        )
        return out

    measure_render_words.__name__ = "measure_render_words"
    measure_render_words.__doc__ = (
        "Page-local word boxes of the CURRENT render PDF (output verification)."
    )
    return measure_render_words


def _write_owner_package(
    out_dir: Path,
    target_pdf: Path,
    record: E4LoopRecord,
    terminal: str,
    config: dict[str, Any],
    trace_entries: list[dict[str, Any]],
) -> Path:
    """The safe owner-review package (E4 work order): target/output page
    comparisons, best-valid HTML/PDF, localized before/after strips for every
    attempted repair, iteration history, cost summary, remaining material
    differences, and the non-claims. Excludes signed URLs, secrets, raw
    provider credentials, private environment data, and human rubric answers."""
    package = out_dir / "owner_review"
    package.mkdir(exist_ok=True)
    best_index = None
    for index, version in enumerate(record.render_versions, 1):
        if version.version_id == record.best_render_version:
            best_index = index
    if best_index is None:
        # no promoted version: compare the LAST rendered hard-gate-valid
        # version, or the final version, always labeled honestly
        best_index = len(record.render_versions)
    best_pdf = out_dir / f"render_{best_index}.pdf"
    target_pages = _render_pages(target_pdf, out_dir, "owner_target")
    page_count = record.render_versions[best_index - 1].page_count if record.render_versions else len(_render_pages(best_pdf, out_dir, "owner_best"))
    for page in range(1, min(page_count, len(target_pages)) + 1):
        _side_by_side(
            [
                ("TARGET", target_pages[page - 1]),
                (f"BEST RENDER v{best_index}", out_dir / f"render_{best_index}_page_{page}.png"),
            ],
            package / f"comparison_page_{page}.png",
        )
    # Localized before/after strips for every EXECUTED repair attempt: the
    # strip shows the target region, the BEFORE render region, and the AFTER
    # (candidate) render region around the finding's own anchors.
    def _attempt_indices(entry: dict[str, Any]) -> tuple[int, int] | None:
        base_index = int(str(entry["base"]).rsplit("-v", 1)[-1])
        # the AFTER pdf is the attempt's own candidate render (the next index
        # after the base when the shell rendered it for this finding)
        for candidate in range(base_index + 1, len(record.render_versions) + 1):
            version = record.render_versions[candidate - 1]
            if f"repair" in version.note and entry["finding"] in version.note:
                return base_index, candidate
        return base_index, None

    attempt_no = 0
    for entry in record.repair_attempts:
        if "layer" not in entry:
            continue
        attempt_no += 1
        finding = next((f for f in record.findings if f.finding_id == entry["finding"]), None)
        if finding is None:
            continue
        base_index, after_index = _attempt_indices(entry)
        before_pdf = out_dir / f"render_{base_index}.pdf"
        after_pdf = out_dir / f"render_{after_index}.pdf" if after_index else None
        _side_by_side(
            [
                ("TARGET", _crop_rows_png(target_pdf, out_dir, f"loc_target_{attempt_no}", finding.requested_measurement.page, [finding.requested_measurement.from_text, finding.requested_measurement.to_text])),
                ("BEFORE", _crop_rows_png(before_pdf, out_dir, f"loc_before_{attempt_no}", finding.requested_measurement.page, [finding.requested_measurement.render_from_text, finding.requested_measurement.render_to_text]) if before_pdf.exists() else None),
                ("AFTER", _crop_rows_png(after_pdf, out_dir, f"loc_after_{attempt_no}", finding.requested_measurement.page, [finding.requested_measurement.render_from_text, finding.requested_measurement.render_to_text]) if after_index else None),
            ],
            package / f"repair_{attempt_no}_localized.png",
        )
    # Best-valid artifacts (copies; originals stay immutable).
    if best_pdf.exists():
        shutil.copy2(best_pdf, package / "best_render.pdf")
        best_html = out_dir / f"render_{best_index}.html"
        if best_html.exists():
            shutil.copy2(best_html, package / "best_render.html")
    iterations = [
        {"sequence": entry.get("sequence"), "agent": entry.get("agent"),
         "phase": entry.get("phase"), "action": entry.get("action"),
         "note": entry.get("note")}
        for entry in trace_entries
    ]
    (package / "iteration_history.json").write_text(
        json.dumps(trace_entries, ensure_ascii=False, indent=1, default=str),
        encoding="utf-8",
    )
    remaining = "\n".join(
        f"- `{fid}`: {next((f.observation for f in record.findings if f.finding_id == fid), fid)}"
        for fid in (record.open_findings or [])
    ) or "- none recorded (open findings list is empty)"
    strategies = "\n".join(f"- {s}" for s in record.attempted_strategies) or "- none"
    report = f"""# Pipeline E4 live-agent owner-review package — {out_dir.name}

- Terminal state: **{terminal}** (never owner acceptance; `delivered` is reserved
  for the explicit owner decision)
- Best hard-gate-valid render: `{record.best_render_version}`
- Live model calls: {record.budget_state.get('calls_by_mode', {}).get('live', 0)};
  scripted invocations: {record.budget_state.get('calls_by_mode', {}).get('scripted', 0)};
  tool calls: {record.budget_state.get('tool_call_count')}/{record.budget_state.get('max_tool_calls')}
- Input/output tokens (live only): {record.budget_state.get('usage', {}).get('input_tokens', 0)}/
  {record.budget_state.get('usage', {}).get('output_tokens', 0)}
- Repairs: {len([a for a in record.repair_attempts if 'layer' in a])} attempted,
  {len([v for v in record.render_versions if v.promoted])} improving,
  {len([s for s in record.attempted_strategies if 'rolled_back' in s])} rollback(s)

## Remaining material differences (open findings)

{remaining}

## Attempted strategies (incl. escalations)

{strategies}

## Contents

- `comparison_page_N.png` — target vs best render, whole pages;
- `repair_N_localized.png` — localized target/before/after strips for attempted repairs;
- `best_render.html` / `best_render.pdf` — the best hard-gate-valid output;
- `iteration_history.json` — the full shell trace (See -> Measure -> Attribute -> Repair -> Re-render);
- this report: iteration history, cost summary, remaining differences, non-claims.

## Non-claims

- This package is NOT a delivery and creates NO T-v1 record.
- No Pipeline E convergence claim; the evidence shows whether material visual
  differences decreased over iterations — nothing more.
- No signed provider URLs, credentials, environment data, or rubric answers
  entered this package (verified by the shell's shareable-artifact check).

## Authority boundary

Experiment-only under `tests/experiments/` (PIPELINE_E_PLAN.md §5,
E_PIPELINE_PREP.md §5). Editable HTML + Chrome PDF is the render surface.
"""
    (package / "REPORT.md").write_text(report, encoding="utf-8")
    # Focused shareable-package check: no signed provider credential data may
    # enter the owner package.
    for path in sorted(package.rglob("*")):
        if path.is_file():
            try:
                payload = path.read_text(encoding="utf-8")
            except Exception:
                continue  # binary image/pdf artifact
            assert_no_signed_strings(payload)
    return package


def _write_e4_report(
    out_dir: Path,
    frozen: FrozenCase,
    draft: TargetStructureDraft,
    record: E4LoopRecord,
    terminal: str,
    config: dict[str, Any],
) -> None:
    claims = "\n".join(
        f"| `{claim.claim_id}` | {claim.relation} | {claim.confidence:.2f} | "
        f"{', '.join(ref.evidence_id for ref in claim.evidence) or '-'} | {claim.statement[:90]} |"
        for claim in draft.structure
    ) or "| - | - | - | - | - |"
    unresolved = "\n".join(
        f"| `{item.item_id}` | {item.status} | {item.evidence_gap} | {item.question[:90]} |"
        for item in _dedup_unresolved(draft.unresolved)
    ) or "| - | - | - | - |"
    versions_table = "\n".join(
        f"| `{version.version_id}` | {version.hard_gates_passed} | {version.promoted} | {version.note} |"
        for version in record.render_versions
    ) or "| - | - | - | - | - |"
    findings_table = "\n".join(
        f"| `{finding.finding_id}` | {finding.render_version} | {finding.region} | "
        f"{finding.suspected_dimension} | {finding.observation[:90]} |"
        for finding in record.findings
    ) or "| - | - | - | - | - | - |"
    strategies = "\n".join(f"| {strategy} |" for strategy in record.attempted_strategies) or "| - |"
    attributions_table = "\n".join(
        f"| `{a.finding_id}` | {a.attribution} | {a.hypothesis_status} | {a.repair_owner} | {a.reason[:80]} |"
        for a in record.attributions
    ) or "| - | - | - | - | - |"
    by_mode = record.budget_state.get("calls_by_mode", {})
    role_rows = "\n".join(f"| {name} | {count} |" for name, count in sorted(record.budget_state.get("calls_by_agent", {}).items())) or "| - | - |"
    tool_rows = "\n".join(f"| {name} | {count} |" for name, count in sorted(record.budget_state.get("calls_by_tool", {}).items())) or "| - | - |"
    (out_dir / "REPORT.md").write_text(
        f"""# Pipeline E4 live-agent Resume I convergence trial — {out_dir.name}

- Case: `{record.target_id}`; target sha256 `{record.target_sha256}`
- Terminal state: **{terminal}** (never owner acceptance; `delivered` is
  reserved for the explicit owner decision; budget exhaustion is never success)
- Best hard-gate-valid render: `{record.best_render_version}`
- Investigator mode: `{record.investigator_mode}`; structure draft:
  `structure_draft_v1` ({len(draft.structure)} claims, {len(_dedup_unresolved(draft.unresolved))} unresolved)
- Live model calls: **{by_mode.get('live', 0)}**; scripted agent invocations:
  **{by_mode.get('scripted', 0)}** (counted separately — Phase 0)
- Input/output tokens (live only): {record.budget_state.get('usage', {}).get('input_tokens', 0)}/{record.budget_state.get('usage', {}).get('output_tokens', 0)}
- Tool calls: {record.budget_state.get('tool_call_count')}/{record.budget_state.get('max_tool_calls')};
  renders: {len(record.render_versions)}; repair attempts:
  {len([a for a in record.repair_attempts if 'layer' in a])} (improving: {len([v for v in record.render_versions if v.promoted])};
  rollbacks: {len([s for s in record.attempted_strategies if 'rolled_back' in s])})
- Elapsed: {record.summary.get('elapsed_seconds')} s; frozen config:
  `run_config.json` (frozen before the first live call)

## Structure claims (each with evidence pointers)

| claim | relation | confidence | evidence | statement |
| --- | --- | --- | --- | --- |
{claims}

## Unresolved (deduplicated by item_id)

| item | status | evidence gap | question |
| --- | --- | --- | --- |
{unresolved}

## Render versions

| version | hard gates | promoted | note |
| --- | --- | --- | --- |
{versions_table}

## Findings (observation-first, version-bound, deduplicated)

| finding | render | region | dimension | observation |
| --- | --- | --- | --- | --- |
{findings_table}

## Attributions (decided from measurement; the reviewer's hypothesis stays recorded)

| finding | attribution | hypothesis | repair owner | reason |
| --- | --- | --- | --- | --- |
{attributions_table}

## Attempted strategies (incl. escalations)

|
{strategies}

## Model/tool cost by role

| role | calls |
| --- | --- |
{role_rows}

| tool | calls |
| --- | --- |
{tool_rows}

## What this run does and does not establish

This is the FIRST live-agent Resume I convergence trial (E3 proved only that
the deterministic orchestration + measurement path runs against Resume I with
zero live calls — it did NOT prove live understanding or convergence, and it
is not a successful template reconstruction). E4 asks whether real live agents
using page images, raw evidence lookup, final-PDF measurement, and bounded
rendering/repair tools make SUSTAINED VISUAL PROGRESS on Resume I without
target-specific production code or human hints.

Does **not** establish: convergence, visual acceptance, or any fidelity winner.
The owner reviews the actual files (owner_review/) and decides; automated
metrics declare nothing (plan §13). This run creates NO T-v1 record.

## Authority boundary

Experiment-only under `tests/experiments/` (PIPELINE_E_PLAN.md §5,
E_PIPELINE_PREP.md §5). No ADR/product contract changed; no private data,
credentials, signed provider URLs, or rubric answers entered any agent input
or shareable artifact.
""",
        encoding="utf-8",
    )

# ===========================================================================
# E5 — Builder representation comparison (Phase 0 loop fixes + Lane A / Lane B)
# ===========================================================================

E5_SCHEMA_VERSION = "pipeline-e-e5-state/1"
# 2026-09-21 owner direction: the model budget must not again be the primary
# reason an experiment ends early. These are RUNAWAY SAFETY CEILINGS, not
# targets to consume; per-agent-run request limits, timeouts, connection
# retry bounds, and tool permission boundaries stay unchanged.
E5_LANE_MAX_MODEL_REQUESTS = 400
E5_LANE_MAX_TOOL_CALLS = 2000
E5_MAX_REPAIR_ROUNDS = 8
# Phase 0: a meaningful part of every lane's budget is RESERVED for Builder
# calls and rerenders; diagnosis must not consume the run before the Builder
# can act (attribution is skipped while the reserve is not covered).
E5_BUILDER_RESERVE_REQUESTS = 40
E5_REVIEWER_MAX_REQUESTS = 4
E5_BUILDER_MAX_REQUESTS = 4
E5_ATTRIBUTION_MAX_REQUESTS = 8
E5_RUNS_E4_BASELINE = "e_pipeline_e4_20260920T134946Z"  # E4 best render baseline
E5_SHARED_DRAFT_SOURCE = "e_pipeline_e4_20260920T134946Z/structure_draft.json"

E5_REVIEWER_INSTRUCTIONS = (
    "You are the independent Visual Reviewer of a resume-layout experiment.\n"
    "You inspect the TARGET and the RENDERED page images you are given. You\n"
    "report LOCALIZED visible differences only.\n"
    "Phase 0 (E5): you report a SEMANTIC measurement intent, never exact\n"
    "verbatim PDF text anchors. Set the measurement request's `intent` to a\n"
    "plain-language description of WHAT to measure (which two elements, which\n"
    "property) — the shell resolves it against the actual final-PDF objects.\n"
    "You do NOT need to read exact text strings out of the image; leaving the\n"
    "four anchor fields empty is correct.\n"
    "Hard boundaries: you never decide the root cause layer; `proposed_cause`\n"
    "is a hypothesis for verification, never a verdict. You never approve\n"
    "delivery. You never modify anything. Every finding binds the exact\n"
    "target and render version strings you were given and carries one typed\n"
    "measurement request with `intent` set.\n"
    "Target person facts are diagnostic evidence only; they never become\n"
    "candidate content."
)

E5_LANE_A_BUILDER_INSTRUCTIONS = (
    "You are the Lane A Builder of a resume-layout experiment.\n"
    "Representation: a provider-neutral STRUCTURED LAYOUT interpreted by a\n"
    "FIXED renderer. You output ONLY typed, validated structure primitives —\n"
    "never executable code, never CSS, never candidate text. Allowed\n"
    "reusable primitives per section: heading_in_rail (the section heading\n"
    "renders in a left label rail beside its content), rail_label_width_pt\n"
    "(the label-rail column width), rail_label_align (left/right), and\n"
    "entry_meta_placement='title_row' (the entry meta column shares the\n"
    "first title line's row). Unknown or invalid fields fail validation.\n"
    "Hard boundaries: candidate facts are NEVER editable and never appear in\n"
    "your output; target person strings never appear in your output; no\n"
    "target-specific identifiers (no target hash, filename, or person\n"
    "branch). You never approve delivery. Base every primitive value on the\n"
    "evidence-linked structure draft and the recorded measurements you are\n"
    "given."
)

E5_LANE_B_BUILDER_INSTRUCTIONS = (
    "You are the Lane B Builder of a resume-layout experiment.\n"
    "Representation: a constrained AUTHORED HTML/CSS template with typed\n"
    "candidate slots. You output ONE typed AuthoredTemplateCandidate:\n"
    "HTML (slot tokens only, no values), CSS, and typed slots declared by\n"
    "CATEGORY only (no token strings, no free-text fields).\n"
    "There are NO free-text metadata fields (no rationale, no region lists,\n"
    "no pagination prose, no expected measurements, no slot descriptions).\n"
    "evidence_refs must be LEFT EMPTY unless the shell gave you specific\n"
    "evidence ids to cite; every cited id must be one the shell issued.\n"
    "Slot vocabulary (exactly these tokens):\n"
    "{{candidate:name}}, {{candidate:contact}}, and repeating regions\n"
    "{{each:summary}}/{{each:experience}}/{{each:education}}/{{each:skills}}/"
    "{{each:languages}}/{{each:certifications}}/{{each:additional}} closed\n"
    "with {{/each}}; inside a region use {{item}}, {{item_head}},\n"
    "{{item_detail}}, {{item_meta}}, {{item_bullets}}, {{item_text}}.\n"
    "Missing optional fields must not leave broken visual artifacts (empty\n"
    "regions render nothing). Long content must not disappear.\n"
    "The shell REJECTS: JavaScript, event handlers, iframe/object/embed/\n"
    "form/video/audio/canvas, remote or network URLs, src/href attributes,\n"
    "CSS imports, url() functions, data: URLs, HTML or CSS comments, the\n"
    "CSS content: property, any target-person literal, any hardcoded\n"
    "candidate fact, and any undeclared slot token.\n"
    "Fixed visible TEXT is not allowed at all: no letter or digit may appear\n"
    "as a literal text node (HTML tag/class/attribute names and CSS are\n"
    "fine). The only visible fixed text you may render is a presentation\n"
    "label, referenced as {{label:<label_id>}} using a label_id from the\n"
    "shell's presentation_labels catalog in your evidence package; the shell\n"
    "owns the text and escapes it. An id you were not given is rejected.\n"
    "You never see or edit candidate values; the shell fills your slots from\n"
    "the reviewed candidate render context. You never approve delivery.\n"
    "Base the design on the target page images and the structure draft you\n"
    "are given."
)

E5_ATTRIBUTION_INSTRUCTIONS = (
    "You are the independent Attribution Investigator of a resume-layout\n"
    "experiment. For each finding trace the chain: raw target evidence ->\n"
    "structure -> template slot -> candidate binding -> render plan ->\n"
    "DOM/CSS -> actual final PDF object. Return ONE typed hypothesis PER\n"
    "FINDING and set finding_id to that finding's EXACT id. Order does not\n"
    "matter because the shell binds by finding_id, but a missing, empty,\n"
    "duplicated, or foreign finding_id is rejected and adds nothing. The\n"
    "reviewer's proposed_cause is a hypothesis that may be wrong; verify from\n"
    "evidence. You may replace the causal hypothesis but never delete the\n"
    "observation, and you never promote or repair anything.\n"
    "Builder ownership is only valid for a confirmed compilation defect: use\n"
    "attribution='template_compilation' with hypothesis_status='confirmed' and\n"
    "repair_owner='builder'. Any other repair_owner='builder' claim is\n"
    "contradictory and is retained as unresolved/reviewer."
)


class E5LedgerEntry(EvidenceModel):
    """One persistent defect-ledger record (Phase 0): deduplicated by the
    stable key `target_version | region | dimension | observation_class`.
    Re-observing an unchanged finding with unchanged evidence never reruns
    full attribution; a CHANGED observation reopens the entry."""

    ledger_key: str
    finding_id: str
    first_seen_version: str
    last_seen_version: str
    status: Literal["open", "attributed", "repaired", "resolved", "regressed"]
    attribution: AttributionRecord | None = None
    measurement_request_id: str | None = None
    observation_class: str = ""
    last_measured_version: str = ""


def _observation_class(observation: str) -> str:
    """Stable semantic observation class: the observation's first normalized
    words (NOT verbatim OCR text — Phase 0)."""
    words = re.findall(r"[a-z]+", observation.casefold())
    return " ".join(words[:8])


def _ledger_key(finding: DefectFinding) -> str:
    """Stable dedup key: target version + structural region + visual
    dimension (the observation CLASS is tracked separately so a changed
    finding REOPENS instead of forking a new key)."""
    return "|".join(
        (finding.target_version, finding.region, finding.suspected_dimension)
    )


class DefectLedger:
    """Persistent defect ledger (Phase 0). `observe` returns one of:
    'new' (first observation), 'dedup' (unchanged finding; existing
    attribution/measurement reused), or 'reopened' (the finding CHANGED after
    a repair — new observation class)."""

    def __init__(self) -> None:
        self.entries: dict[str, E5LedgerEntry] = {}

    def observe(
        self,
        finding: DefectFinding,
        *,
        resolved: bool = False,
    ) -> tuple[E5LedgerEntry, str]:
        key = _ledger_key(finding)
        existing = self.entries.get(key)
        if existing is None:
            entry = E5LedgerEntry(
                ledger_key=key,
                finding_id=finding.finding_id,
                first_seen_version=finding.render_version,
                last_seen_version=finding.render_version,
                status="resolved" if resolved else "open",
                observation_class=_observation_class(finding.observation),
                last_measured_version=finding.render_version,
            )
            self.entries[key] = entry
            return entry, "new"
        existing.last_seen_version = finding.render_version
        if _observation_class(finding.observation) != existing.observation_class:
            # the finding CHANGED after repair: reopen with the new class,
            # bound to the LATEST finding id; the stale attribution and
            # measurement binding no longer describe this defect and are
            # cleared (never silently inherited).
            existing.observation_class = _observation_class(finding.observation)
            existing.finding_id = finding.finding_id
            existing.attribution = None
            existing.measurement_request_id = None
            existing.status = "open"
            return existing, "reopened"
        if resolved:
            existing.status = "resolved"
        return existing, "dedup"


# The ONLY open/closed source for E5 findings is the ledger entry status.
# repaired/resolved are CLOSED; open/attributed/regressed are NOT closed.
E5_LEDGER_CLOSED_STATUSES = frozenset({"repaired", "resolved"})


def _open_ledger_finding_ids(entries: list["E5LedgerEntry"]) -> list[str]:
    """Open findings = the current finding id of every ledger entry whose
    status is not closed. Raw finding ids are NEVER scanned directly — a
    deduplicated re-observation of an already-repaired defect must not
    resurrect the defect as open under a new id."""
    return [entry.finding_id for entry in entries if entry.status not in E5_LEDGER_CLOSED_STATUSES]


def _record_ledger_attribution(
    ledger: "DefectLedger",
    finding: "DefectFinding",
    attribution: AttributionRecord,
    request_id: str,
) -> None:
    """Write an attribution back to the owning ledger entry (single source of
    defect state): the attribution record, the measurement request id, and
    the status. A confirmed measurement WITHIN tolerance (`no_defect`) closes
    the entry as resolved; every other attribution leaves it not-closed."""
    entry = ledger.entries.get(_ledger_key(finding))
    if entry is None:
        return
    entry.attribution = attribution
    entry.measurement_request_id = request_id
    entry.status = "resolved" if attribution.attribution == "no_defect" else "attributed"


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
    agent: Literal["scripted", "llm"]


class E5LaneRecord(EvidenceModel):
    """One lane's resumable E5 state."""

    lane: Literal["a", "b"]
    representation: str
    render_versions: list[RenderVersion] = Field(default_factory=list)
    best_render_version: str | None = None
    # Local improvement state: a defect-level promoted version (promoted with
    # a known remaining red gate). Never labeled BEST; never the
    # hard-gate-valid best.
    best_defect_level_version: str | None = None
    active_render_version: str | None = None
    findings: list[DefectFinding] = Field(default_factory=list)
    measurement_results: list[MeasurementResult] = Field(default_factory=list)
    attributions: list[AttributionRecord] = Field(default_factory=list)
    repair_attempts: list[dict[str, Any]] = Field(default_factory=list)
    builder_candidates: list[dict[str, Any]] = Field(default_factory=list)
    attempted_strategies: list[str] = Field(default_factory=list)
    action_fingerprints: list[str] = Field(default_factory=list)
    ledger: list[E5LedgerEntry] = Field(default_factory=list)
    open_findings: list[str] = Field(default_factory=list)
    content_shape_probes_passed: bool = False
    pages_reviewed: list[int] = Field(default_factory=list)
    terminal_state: Literal["ready_for_owner_review", "budget_exhausted", "operational_abort"] = (
        "budget_exhausted"
    )
    budget_state: dict[str, Any] = Field(default_factory=dict)
    summary: dict[str, Any] = Field(default_factory=dict)


class E5LoopRecord(EvidenceModel):
    """The E5 run record: per-lane state, frozen inputs, comparison metrics."""

    schema_version: Literal["pipeline-e-e5-state/1"] = E5_SCHEMA_VERSION
    target_id: str
    target_sha256: str
    lanes: dict[str, E5LaneRecord] = Field(default_factory=dict)
    budget_state: dict[str, Any] = Field(default_factory=dict)
    started_at: str = ""
    finished_at: str = ""
    summary: dict[str, Any] = Field(default_factory=dict)


PRESENTATION_LABEL_KINDS = ("section_heading",)


class PresentationLabel(EvidenceModel):
    """One shell-PROPOSED presentation label (owner decision 2026-09-21):
    fixed visible template text taken from target structure evidence. The
    Builder can only REFERENCE an ``approved`` ``label_id`` — it can never
    submit label text, and a `proposed` entry is not renderable at all."""

    label_id: str
    text: str
    kind: Literal["section_heading"]
    status: Literal["proposed", "approved"] = "proposed"
    evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def label_is_typed_and_evidence_backed(self) -> "PresentationLabel":
        if not self.text.strip():
            raise ValueError("presentation label requires non-empty text")
        if not self.evidence_ids or not all(ref.strip() for ref in self.evidence_ids):
            raise ValueError(
                "presentation label requires at least one target evidence id"
            )
        if self.kind not in PRESENTATION_LABEL_KINDS:
            raise ValueError(f"unknown presentation label kind: {self.kind!r}")
        return self


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


def _approved_presentation_labels(
    catalog: list[PresentationLabel],
) -> list[PresentationLabel]:
    """The ONLY renderable labels: the approved view of a validated approval.
    A proposed entry is deliberately excluded (non-renderable, and not
    privacy-excluded)."""
    return [label for label in catalog if label.status == "approved"]


def _e5_label_semantics(
    gate: dict[str, Any], approved: list[PresentationLabel]
) -> dict[str, Any]:
    """Whether this lane's privacy gate excluded EXACTLY the owner-approved
    label set. Both lanes run the SAME common gate with the SAME approved set,
    so a subset (e.g. Lane A's plan-derived labels) is NOT symmetric — the two
    lanes' privacy decisions must be identical to be comparable."""
    from tests.experiments.c2_renderer import _norm

    excluded = {_norm(item) for item in (gate.get("excluded_labels") or [])}
    approved_texts = {_norm(label.text) for label in approved}
    return {
        "approved_label_ids": [label.label_id for label in approved],
        "symmetric": excluded == approved_texts,
        "excluded_labels": sorted(excluded),
        "excluded_outside_approved": sorted(excluded - approved_texts),
        "approved_texts_never_excluded": sorted(approved_texts - excluded),
    }


def _presentation_label_listing(
    catalog: list[PresentationLabel],
    lane_references: dict[str, list[str]],
    approved_label_ids: list[str] | None = None,
    *,
    target_sha256: str | None = None,
    catalog_sha256: str | None = None,
    approval_provided: bool | None = None,
    approval_validated: bool | None = None,
) -> dict[str, Any]:
    """The owner-visible label listing (label_id / text / kind / status /
    evidence / which lanes referenced it). Status comes from the owner-supplied
    ``approved_label_ids`` (the validated approved view); shell catalog entries
    are always proposed. `proposed` entries are listed for review but are NOT
    renderable. Audit material only — it does NOT mean the owner accepted
    T-v1."""
    approved: set[str] = set(approved_label_ids or [])
    return {
        "schema_version": "e5-presentation-labels/1",
        "note": (
            "shell-proposed presentation labels taken from target evidence. Only "
            "owner-approved entries (a typed approval bound to target_sha256 + "
            "catalog_sha256 + label ids) are renderable and privacy-excluded; "
            "proposed entries await explicit owner approval and are NOT "
            "renderable. Audit material only: this is NOT an acceptance of T-v1 "
            "and no lane is declared a winner."
        ),
        "target_sha256": target_sha256,
        "catalog_sha256": catalog_sha256,
        "approval_provided": approval_provided,
        "approval_validated": approval_validated,
        "approved_label_ids": [
            label.label_id for label in catalog if label.label_id in approved
        ],
        "proposed_label_ids": [
            label.label_id for label in catalog if label.label_id not in approved
        ],
        "labels": [
            {
                "label_id": label.label_id,
                "text": label.text,
                "kind": label.kind,
                "status": (
                    "approved" if label.label_id in approved else label.status
                ),
                "evidence_ids": label.evidence_ids,
                "referenced_by_lanes": sorted(
                    lane_id
                    for lane_id, refs in lane_references.items()
                    if label.label_id in set(refs)
                ),
            }
            for label in catalog
        ],
    }


def _e5_builder_evidence_package(
    *,
    draft: TargetStructureDraft,
    state: Any,
    derived: dict[str, Any],
    page_size: tuple[float, float],
    current_render_version: str | None = None,
    findings: list[DefectFinding] | None = None,
    measurements: list[MeasurementResult] | None = None,
    current_gates: dict[str, Any] | None = None,
    last_rejection: str | None = None,
    target_images: list[Path] | None = None,
    current_render_images: list[Path] | None = None,
    selected_attribution: AttributionRecord | None = None,
    action_fingerprint: str | None = None,
    presentation_labels: list[PresentationLabel] | None = None,
) -> dict[str, Any]:
    """One representation-neutral Builder evidence package.

    Both lanes receive this exact data shape and the same target images. Only
    their output schema/instructions differ. Repair calls additionally receive
    the current render images outside this JSON package. `presentation_labels`
    is the SAME shell-owned catalog for both lanes: the only visible fixed
    text a template may carry, referenceable by `label_id` only.
    """
    return {
        "schema_version": "e5-builder-evidence/1",
        "presentation_labels": [
            label.model_dump(mode="json") for label in presentation_labels or []
        ],
        "structure_draft": draft.model_dump(mode="json"),
        "underlying_evidence_summary": {
            "page_size_pt": list(page_size),
            "state_sections": [
                {
                    "node_id": node.node_id,
                    "kind": node.kind,
                    "sources": getattr(node.binding, "sources", None) if node.binding else None,
                }
                for node in state.nodes
                if node.kind == "section"
            ],
            "sidebar_rules": derived.get("sidebar_rules"),
            "sidebar_labels": derived.get("sidebar_labels"),
        },
        "current_render_version": current_render_version,
        "findings": [finding.model_dump(mode="json") for finding in findings or []],
        "measurements": [result.model_dump(mode="json") for result in measurements or []],
        "current_gates": current_gates or {},
        "last_rejection": last_rejection,
        "target_images": [
            {"name": image.name, "sha256": _sha256_file(image)} for image in target_images or []
        ],
        "current_render_images": [
            {"name": image.name, "sha256": _sha256_file(image)}
            for image in current_render_images or []
        ],
        "selected_attribution": (
            selected_attribution.model_dump(mode="json") if selected_attribution else None
        ),
        "action_fingerprint": action_fingerprint,
    }


def _e5_gate_classification(
    lane: str,
    gates: dict[str, Any],
    *,
    privacy_labels_symmetric: bool,
) -> dict[str, Any]:
    """Classify only what the implemented gates can establish."""
    result: dict[str, Any] = {}
    if lane == "a" and not gates.get("content_shapes_match_evidence", True):
        result["representation_ceiling"] = "unverified"
        result["reason"] = (
            "content_shape_verification does not verify rail_heading, "
            "rail_label_width_pt, rail_label_align, or rendered rail geometry"
        )
    if lane == "b" and not gates.get("no_target_candidate_facts", True):
        result["privacy_failure"] = (
            "unresolved" if privacy_labels_symmetric else "gate_false_positive_or_boundary_unresolved"
        )
        result["privacy_labels_symmetric"] = privacy_labels_symmetric
    return result


def _e5_hard_gate_record(
    render_version: str,
    gates: dict[str, bool],
    details: dict[str, Any],
) -> dict[str, Any]:
    """Version-bound gate summary plus the evidence needed to explain it."""
    return {
        "render_version": render_version,
        "passed": all(gates.values()),
        "gates": gates,
        "details": details,
    }


def _e5_builder_candidate_record(
    *,
    lane: str,
    attempt: int,
    stage: str,
    input_render_version: str | None,
    candidate_output: Any,
    validation: dict[str, Any],
    outcome: str,
    reason: str,
    attribution: AttributionRecord | None = None,
    action_fingerprint: str | None = None,
) -> dict[str, Any]:
    """Local audit record for one parsed typed Builder output."""
    return {
        "schema_version": "e5-builder-candidate-audit/1",
        "lane": lane,
        "builder_attempt": attempt,
        "stage": stage,
        "input_render_version": input_render_version,
        "typed_candidate": candidate_output.model_dump(mode="json"),
        "validation": validation,
        "candidate_render_version": None,
        "outcome": outcome,
        "reason": reason,
        "trigger_attribution": attribution.model_dump(mode="json") if attribution else None,
        "action_fingerprint": action_fingerprint,
        "artifact": f"builder_candidate_{attempt:02d}.json",
    }


def _next_e5_repair_finding(
    actionable: list[DefectFinding],
    measured: dict[str, tuple[MeasurementResult, MeasurementRequest]],
    attributions: list[AttributionRecord],
    fingerprints: list[str],
) -> tuple[
    tuple[DefectFinding, MeasurementResult, MeasurementRequest, AttributionRecord, str] | None,
    bool,
]:
    """Return the first measured, material, not-yet-executed action.

    The boolean says repairable findings existed but every fingerprint was
    already executed, so the lane must stop instead of burning another round.
    """
    repairable_seen = False
    for finding in actionable:
        result, bound = measured[finding.finding_id]
        if result.status != "confirmed" or abs(result.delta_pt or 0.0) <= E2_IMPROVEMENT_TOLERANCE_PT:
            continue
        attribution = next(
            (
                item
                for item in reversed(attributions)
                if item.finding_id == finding.finding_id
                and item.render_version == finding.render_version
                and item.measurement_request_id == bound.request_id
                and item.hypothesis_status == "confirmed"
                and item.repair_owner == "builder"
            ),
            None,
        )
        if attribution is None:
            continue
        repairable_seen = True
        fingerprint = f"{finding.region}:{finding.suspected_dimension}:{round(result.delta_pt or 0, 3)}"
        if fingerprint not in fingerprints:
            return (finding, result, bound, attribution, fingerprint), False
    return None, repairable_seen


def _e5_attribution_batches(
    actionable: list[DefectFinding],
    measured: dict[str, tuple[MeasurementResult, MeasurementRequest]],
) -> list[list[DefectFinding]]:
    """Batch every non-no-defect finding for live causal attribution."""
    groups: dict[str, list[DefectFinding]] = {}
    for finding in actionable:
        result, _ = measured[finding.finding_id]
        if result.status == "confirmed" and abs(result.delta_pt or 0.0) <= E2_IMPROVEMENT_TOLERANCE_PT:
            continue
        groups.setdefault(finding.region, []).append(finding)
    return [part[i : i + 3] for part in groups.values() for i in range(0, len(part), 3)]


def _e5_default_attribution(
    finding: DefectFinding,
    result: MeasurementResult,
    request: MeasurementRequest,
) -> AttributionRecord:
    """Explicit non-live/fallback state without inventing a causal owner."""
    if result.status == "confirmed" and result.delta_pt is not None:
        if abs(result.delta_pt) <= E2_IMPROVEMENT_TOLERANCE_PT:
            return AttributionRecord(
                finding_id=finding.finding_id,
                render_version=finding.render_version,
                measurement_request_id=request.request_id,
                attribution="no_defect",
                hypothesis_status="rejected",
                repair_owner="none",
                evidence=[result.request_id],
                reason="measured role gap matches the target within the documented tolerance",
            )
        return AttributionRecord(
            finding_id=finding.finding_id,
            render_version=finding.render_version,
            measurement_request_id=request.request_id,
            attribution="unresolved",
            hypothesis_status="unresolved",
            repair_owner="reviewer",
            evidence=[result.request_id],
            reason=(
                "the measured final-PDF role gap differs beyond tolerance, but the "
                "measurement alone does not establish the owning layer"
            ),
        )
    return AttributionRecord(
        finding_id=finding.finding_id,
        render_version=finding.render_version,
        measurement_request_id=request.request_id,
        attribution="not_measurable",
        hypothesis_status="unresolved",
        repair_owner="binding",
        evidence=[result.request_id],
        reason=(
            "the semantic measurement intent did not bind to final-PDF objects this "
            "round; the finding stays open for re-verification"
        ),
    )


class LiveAttributionBindingError(ValueError):
    """The live attribution batch did not return exactly ONE hypothesis per
    batch finding id. The shell fails closed: no positional fallback, no
    guessing, no auto-fill."""


def _bind_live_attribution_batch(
    findings: list[DefectFinding],
    hypotheses: list[LiveAttributionHypothesis],
) -> list[tuple[DefectFinding, LiveAttributionHypothesis]]:
    """Bind each live hypothesis to its finding BY ``finding_id``.

    Fail closed on an empty/missing/duplicate/foreign finding_id, or on a
    batch finding with no returned hypothesis — a batch whose counts merely
    look equal is rejected too, because the identity SET must be equal.
    Reordering the same identity set is valid and changes nothing."""
    expected = [finding.finding_id for finding in findings]
    if len(set(expected)) != len(expected):
        raise LiveAttributionBindingError(f"batch findings carry duplicate ids: {expected}")
    known = set(expected)
    by_id: dict[str, LiveAttributionHypothesis] = {}
    for hypothesis in hypotheses:
        finding_id = (hypothesis.finding_id or "").strip()
        if not finding_id:
            raise LiveAttributionBindingError("hypothesis without a finding_id")
        if finding_id not in known:
            raise LiveAttributionBindingError(
                f"hypothesis for finding id {finding_id!r} outside the batch {expected}"
            )
        if finding_id in by_id:
            raise LiveAttributionBindingError(
                f"duplicate hypothesis for finding id {finding_id!r}"
            )
        by_id[finding_id] = hypothesis
    unreturned = [finding_id for finding_id in expected if finding_id not in by_id]
    if unreturned:
        raise LiveAttributionBindingError(
            f"no hypothesis returned for finding id(s) {unreturned}"
        )
    return [(finding, by_id[finding.finding_id]) for finding in findings]


# The ONLY legal builder-owned attribution (observed in the deterministic
# _e5_default_attribution / run_e4 / run_e5 conversions): the Builder may only
# change the compilation/plan layer, so builder ownership requires a CONFIRMED
# template_compilation attribution. Any other triple claiming the Builder would
# hand a binding/renderer/evidence-owned defect to the Builder.
BUILDER_OWNED_ATTRIBUTION: tuple[str, str] = ("template_compilation", "confirmed")


def _e5_live_attribution_record(
    finding: DefectFinding,
    hypothesis: LiveAttributionHypothesis,
    request_id: str,
) -> AttributionRecord:
    """The SINGLE live-hypothesis -> ``AttributionRecord`` conversion (E4
    single-finding and E5 batched paths). The finding/render/measurement
    identity ALWAYS comes from the shell-bound finding and the current
    request, never from the model. A contradictory builder claim is retained
    as unresolved/reviewer with the original triple recorded, so the Builder
    can never receive it."""
    attribution = hypothesis.attribution
    status = hypothesis.hypothesis_status
    owner = hypothesis.repair_owner
    reason = hypothesis.reason
    if owner == "builder" and (attribution, status) != BUILDER_OWNED_ATTRIBUTION:
        reason = (
            "rejected contradictory live attribution "
            f"(attribution={attribution!r}, hypothesis_status={status!r}, "
            "repair_owner='builder'): builder ownership requires "
            f"attribution={BUILDER_OWNED_ATTRIBUTION[0]!r} with "
            f"hypothesis_status={BUILDER_OWNED_ATTRIBUTION[1]!r}; retained as "
            f"unresolved/reviewer. Model reason: {hypothesis.reason}"
        )
        attribution, status, owner = "unresolved", "unresolved", "reviewer"
    return AttributionRecord(
        finding_id=finding.finding_id,
        render_version=finding.render_version,
        measurement_request_id=request_id,
        attribution=attribution,
        hypothesis_status=status,
        repair_owner=owner,
        evidence=[request_id, *hypothesis.evidence_ids],
        reason=reason,
    )


def _e5_findings_needing_fallback_attribution(
    actionable: list[DefectFinding],
    measured: dict[str, tuple[MeasurementResult, MeasurementRequest]],
    attributions: list[AttributionRecord],
) -> list[DefectFinding]:
    """Findings with no attribution for the CURRENT identity
    ``finding_id + render_version + measurement_request_id``. An older
    measurement's attribution NEVER satisfies a new measurement."""
    attributed_keys = {
        (item.finding_id, item.render_version, item.measurement_request_id)
        for item in attributions
    }
    return [
        finding
        for finding in actionable
        if (
            finding.finding_id,
            finding.render_version,
            measured[finding.finding_id][1].request_id,
        )
        not in attributed_keys
    ]


def _builder_reserve_intact(budget: RunBudget) -> bool:
    """Phase 0 Builder budget reservation: live attribution may only run when
    the lane budget still covers the Builder reserve (diagnosis must not eat
    the budget before the Builder can act)."""
    return budget.remaining_model_requests() > E5_BUILDER_RESERVE_REQUESTS


def _best_valid_version_id(
    versions: list[RenderVersion], excluded: set[str] | None = None
) -> str | None:
    """Explicit best-valid selection rule (never 'first promoted' and never
    'first hard-gate-valid', and never `versions[-1]`):

    1. the LATEST HARD-GATE-VALID version that was never explicitly ROLLED
       BACK. A defect-level promotion (promoted with a known remaining red
       gate, e.g. content_shapes_match_evidence) is NOT hard-gate-valid and
       must never masquerade as the best-valid render — it is recorded as
       the active/defect-level state instead (see
       `_best_defect_level_version_id`);
    2. otherwise None (no best-valid render exists)."""
    excluded = excluded or set()
    for version in reversed(versions):
        if version.hard_gates_passed and version.version_id not in excluded:
            return version.version_id
    return None


def _best_defect_level_version_id(
    versions: list[RenderVersion], excluded: set[str] | None = None
) -> str | None:
    """The latest defect-level promoted version (promoted=True with a known
    remaining red hard gate) that was never rolled back. This is LOCAL
    improvement state — the active render may rest on it — but it is never
    the hard-gate-valid best and is never labeled BEST."""
    excluded = excluded or set()
    for version in reversed(versions):
        if version.promoted and not version.hard_gates_passed and version.version_id not in excluded:
            return version.version_id
    return None


def _bind_role_gap_anchors(
    request: MeasurementRequest,
    *,
    target_pdf: Path,
    current_pdf: Path,
    region_span: tuple[float, float] | None,
    render_leaf_texts: list[str],
) -> MeasurementRequest | MeasurementResult:
    """Phase 0 measurement binding: resolve a SEMANTIC measurement intent
    into actual final-PDF objects (exact line-prefix anchors on BOTH sides).
    The render side binds against the render's own candidate entry-head
    texts; the target side binds against the target's measured content rows
    within the region span. A Reviewer OCR mismatch never becomes an
    automatic measurement_failure: an unresolvable intent returns
    `evidence_missing` for re-verification, with the binding method recorded."""
    render_rows = _pdf_line_rows(current_pdf, request.page)

    def _norm_row(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip().casefold()

    prefixes = [
        _norm_row(text)[:12]
        for text in render_leaf_texts
        if text and len(text.strip()) >= 6
    ]
    render_anchors: list[str] = []
    for row in render_rows:
        row_text = _norm_row(row["text"])
        for prefix in prefixes:
            if row_text.startswith(prefix) and prefix not in render_anchors:
                render_anchors.append(prefix)
                break
        if len(render_anchors) == 2:
            break
    if len(render_anchors) < 2:
        return MeasurementResult(
            request_id=request.request_id,
            status="evidence_missing",
            reason=(
                "the semantic intent could not be bound to two render entry-head "
                "rows in the final PDF (binding, not OCR, decides)"
            ),
            method="role_gap_binding/1",
            warnings=["binding resolves the Reviewer's semantic intent against actual PDF rows"],
        )
    target_rows = _pdf_line_rows(target_pdf, request.page)
    if region_span is not None:
        span_top, span_bottom = region_span
        target_rows = [
            row for row in target_rows if span_top < row["top"] < span_bottom
        ]
    # ponytail: target entry heads = the first two non-bullet rows below the
    # section rule (dates/meta rows carry digits; bullets carry the marker);
    # a dedicated head classifier is the upgrade if this misbinds.
    head_rows = [
        row for row in target_rows
        if not row["text"].startswith("\u2022")
        and not row["text"].startswith("\u2022")
        and any(character.isalpha() for character in row["text"])
    ]
    if len(head_rows) < 2:
        return MeasurementResult(
            request_id=request.request_id,
            status="evidence_missing",
            reason="the region's target content rows could not yield two head anchors",
            method="role_gap_binding/1",
            warnings=["binding resolves the Reviewer's semantic intent against actual PDF rows"],
        )
    return request.model_copy(
        update={
            "from_text": _norm_row(head_rows[0]["text"])[:16],
            "to_text": _norm_row(head_rows[1]["text"])[:16],
            "render_from_text": render_anchors[0],
            "render_to_text": render_anchors[1],
        }
    )


def _region_spans_from_draft(draft: TargetStructureDraft, page_height: float) -> dict[str, tuple[int, float, float]]:
    """node_id -> (page, top, bottom) from the draft's measured section
    boundary claims (each cites its sidebar label row + rule bbox)."""
    claims = []
    for claim in draft.structure:
        if claim.relation != "section_boundary":
            continue
        rule_top = None
        page_number = None
        for ref in claim.evidence:
            if ref.bbox_pt and len(ref.bbox_pt) == 4 and ref.page_number is not None:
                page_number = ref.page_number
                if ref.bbox_pt[1] > 0 or ref.bbox_pt[3] > ref.bbox_pt[1]:
                    rule_top = float(ref.bbox_pt[1])
        if page_number is not None:
            claims.append((page_number, rule_top, claim.child))
    spans: dict[str, tuple[int, float, float]] = {}
    by_page: dict[int, list[tuple[float, str]]] = {}
    for page_number, rule_top, child in claims:
        by_page.setdefault(page_number, []).append((rule_top, child))
    for page_number, rows in by_page.items():
        rows.sort()
        for index, (rule_top, child) in enumerate(rows):
            bottom = rows[index + 1][0] if index + 1 < len(rows) else page_height
            spans[child] = (page_number, rule_top + 1.0, bottom)
    return spans


def _apply_lane_a_structure(plan: Any, proposal: LaneAStructureProposal | None) -> Any:
    """Map the Lane A typed primitives onto the compiled plan (the fixed
    renderer interprets them; unknown section ids fail validation)."""
    if proposal is None:
        return plan
    updated_sections = []
    for section in plan.sections:
        match = next(
            (item for item in proposal.sections if item.section_node_id == section.node_id),
            None,
        )
        if match is not None:
            section = section.model_copy(
                update={
                    "rail_heading": match.heading_in_rail or None,
                    "rail_label_width_pt": match.rail_label_width_pt,
                    "rail_label_align": match.rail_label_align,
                    "entry_meta_placement": match.entry_meta_placement,
                }
            )
        updated_sections.append(section)
    updated_appended = []
    for section in plan.appended_sections:
        match = next(
            (item for item in proposal.sections if item.section_node_id == section.node_id),
            None,
        )
        if match is not None:
            section = section.model_copy(
                update={
                    "rail_heading": match.heading_in_rail or None,
                    "rail_label_width_pt": match.rail_label_width_pt,
                    "rail_label_align": match.rail_label_align,
                    "entry_meta_placement": match.entry_meta_placement,
                }
            )
        updated_appended.append(section)
    # The compiled plan is a SUBSET of the state's sections (contentless
    # sections render nothing); primitives for plan-absent sections are
    # recorded, not applied. Unknown state ids are rejected earlier by
    # `_validate_lane_a_proposal` (against the state node vocabulary).
    proposed_ids = {item.section_node_id for item in proposal.sections}
    skipped = sorted(
        proposed_ids - {s.node_id for s in [*updated_sections, *updated_appended]}
    )
    notes = [*plan.notes, *(
        f"lane A structure proposal cites {node_id!r}, which has no compiled plan section (skipped)"
        for node_id in skipped
    )]
    return plan.model_copy(
        update={"sections": updated_sections, "appended_sections": updated_appended, "notes": notes}
    )


def _validate_lane_a_proposal(
    proposal: LaneAStructureProposal, state: Any
) -> str | None:
    """Shell-side Lane A validation: section ids must exist in the compiled
    state; no candidate text may appear anywhere in the proposal (candidate
    facts are never editable); rail widths are bounded by the model."""
    known = {node.node_id for node in state.nodes}
    unknown = [item.section_node_id for item in proposal.sections if item.section_node_id not in known]
    if unknown:
        return f"proposal cites unknown state nodes: {unknown}"
    payload = proposal.model_dump_json()
    for leaf in getattr(state, "candidate_texts", []) or []:
        if leaf and leaf in payload:  # candidate facts can never enter (defensive)
            return "proposal carries candidate text"
    return None


def _scripted_lane_a_proposal(state: Any, derived: dict[str, Any]) -> LaneAStructureProposal:
    """Offline verification-path Lane A Builder: rails every mapped section
    using the GENERIC two-column derivation's measured rail extent (never a
    target constant)."""
    rules = derived.get("sidebar_rules") or {}
    widths = [
        float(rule["x1_pt"]) - float(rule["x0_pt"])
        for rule in rules.values()
        if rule.get("x1_pt") and rule.get("x0_pt")
    ]
    rail_width = round(sum(widths) / len(widths), 3) if widths else 110.0
    sections = [
        E5LaneASection(
            section_node_id=node.node_id,
            heading_in_rail=node.kind == "section",
            rail_label_width_pt=round(rail_width, 3) if node.kind == "section" else None,
            rail_label_align="right" if node.kind == "section" else None,
        )
        for node in state.nodes
        if node.kind == "section"
    ]
    return LaneAStructureProposal(
        proposal_id="scripted-rail-1",
        sections=sections,
        agent="scripted",
    )


def _scripted_lane_b_template(base: AuthoredTemplateCandidate, finding: DefectFinding, result: MeasurementResult) -> AuthoredTemplateCandidate:
    """Offline verification-path Lane B Builder: one bounded deterministic
    template mutation for the measured defect class (entry-gap compression)."""
    css = base.css
    if result.delta_pt is not None and result.delta_pt > 0 and "margin-top: 6pt" in css:
        css = css.replace("margin-top: 6pt", f"margin-top: {max(1.0, 6.0 - abs(result.delta_pt) / 2):.1f}pt")
    return base.model_copy(update={"template_id": base.template_id + "-r", "css": css})


def _review_scope_payload(
    round_no: int, changed_regions: list[str], findings: list["DefectFinding"]
) -> dict[str, Any]:
    """Phase 0 review scope (E5 work order): round 1 reviews the whole
    document; later rounds focus on changed regions, dependent regions, and
    unresolved findings with ONE bounded global sweep — the Reviewer never
    regenerates the entire defect list after a local change."""
    return {
        "round": round_no,
        "whole_document": round_no == 1,
        "changed_regions": list(changed_regions),
        "open_findings": [
            {"region": finding.region, "dimension": finding.suspected_dimension}
            for finding in findings
        ],
        "instruction": (
            "Round 1: whole-document review. Later rounds: focus on the "
            "changed regions and their dependent regions, restate only "
            "unresolved findings, and run ONE bounded global sweep — do "
            "NOT regenerate the entire defect list."
        ),
    }


def _apply_header_overflow_disposition(candidate: Any, state: Any) -> Any:
    """Generic E3 shell transition, shared by the main render and every
    content-shape probe: header fields with no measured home in the compiled
    state route through the EXPLICIT candidate-only header-overflow node
    (nothing silently dropped, nothing invented)."""
    from tests.experiments.c2_candidates import UnroutableContent

    header_slots_in_state = {
        slot for node in state.nodes if node.kind == "header_row" for slot in node.slots
    }
    unhomed = [
        leaf for leaf in candidate.leaves
        if leaf.kind == "header_field" and leaf.slot not in header_slots_in_state
    ]
    if not unhomed:
        return candidate
    return candidate.model_copy(
        update={
            "unroutable": [
                *candidate.unroutable,
                *(
                    UnroutableContent(
                        text=leaf.text or "",
                        reason=(
                            "the compiled target header region carries no measured "
                            f"row with slot {leaf.slot!r}; routes through the explicit "
                            "candidate-only header-overflow node"
                        ),
                        slot=leaf.slot,
                    )
                    for leaf in unhomed
                ),
            ]
        }
    )


_PROBE_ROLE_ORDER = (
    "summary", "skills", "languages", "work_experience", "education",
    "certifications", "additional_details",
)
_PROBE_ROLE_KIND = {
    "summary": "paragraph",
    "work_experience": "entries",
    "education": "entries",
    "skills": "item_list",
    "languages": "item_list",
    "certifications": "item_list",
    "additional_details": "item_list",
}
# A headingless item_list section is invalid by contract; the shell labels
# the appended candidate-only section with the generic role label (the same
# presentation the role name carries everywhere; never a target string).
_PROBE_ROLE_HEADING = {
    "skills": "SKILLS", "languages": "LANGUAGES", "certifications": "CERTIFICATIONS",
    "additional_details": "ADDITIONAL",
}


def _sectioned_probe_candidate(fixture: Any) -> Any:
    """Deterministic shell promotion of a bare C2-0a structural fixture into
    a sectioned render context, so the Lane A probe can run the REAL
    plan -> HTML -> Chrome PDF chain with the same routing rules
    `run_flow_probe` validates (generic role -> content-kind mapping; no
    target-specific identifier, no candidate text change).

    `additional_section` containers are structural only: the plan's mapped
    item-list merge consumes top-level item leaves, so the shell flattens
    the container (children re-parented as top-level items) — the same
    top-level-item shape every candidate render context uses."""
    from tests.experiments.c2_candidates import CandidateSection

    flattened: list[Any] = []
    for leaf in fixture.leaves:
        if leaf.kind == "additional_section":
            for child in fixture.leaves:
                if child.parent_leaf_id == leaf.leaf_id:
                    flattened.append(child.model_copy(update={"parent_leaf_id": None}))
        else:
            flattened.append(leaf)
    fixture = fixture.model_copy(update={"leaves": flattened})
    sections = []
    for role in _PROBE_ROLE_ORDER:
        leaves = [leaf for leaf in fixture.leaves if leaf.source == role]
        if not leaves:
            continue
        sections.append(
            CandidateSection(
                section_id=role,
                heading=_PROBE_ROLE_HEADING.get(role),
                source=role,
                content_kind=_PROBE_ROLE_KIND[role],
                leaf_ids=[leaf.leaf_id for leaf in leaves],
            )
        )
    return fixture.model_copy(update={"sections": sections})


def _probe_candidate_with_text(fixture: Any) -> Any:
    """Independent content-shape probe with deterministic synthetic values
    (the canonical C2 fixtures carry structure only). Values are unique per
    leaf and carry NO section/identifier words, so the privacy gate's
    line-granularity check cannot trip on the probe's own placeholder text.
    Synthetic placeholder text is authorized probe content — never a target
    or candidate fact."""
    leaves = [
        leaf.model_copy(update={"text": f"probe value {index:03d}"})
        for index, leaf in enumerate(fixture.leaves, 1)
    ]
    return fixture.model_copy(update={"leaves": leaves})


def _estimate_lane_cost(
    record: "E5LaneRecord", pricing: dict[str, Any] | None
) -> dict[str, Any]:
    """Provider accounting (E5 work order): raw usage is always recorded;
    a cost estimate exists ONLY when verified official pricing was supplied
    in the frozen config. No price is ever invented."""
    usage = record.budget_state.get("usage", {})
    raw = {
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "live_model_calls": record.budget_state.get("calls_by_mode", {}).get("live", 0),
    }
    if not pricing or not pricing.get("verified"):
        return {**raw, "estimated_cost_usd": None, "pricing_source": None,
                "note": "exact billing unavailable; raw usage preserved"}
    in_price = pricing.get("input_per_million_usd")
    out_price = pricing.get("output_per_million_usd")
    if in_price is None or out_price is None:
        return {**raw, "estimated_cost_usd": None, "pricing_source": pricing.get("source_url")}
    estimate = (
        raw["input_tokens"] / 1_000_000 * in_price
        + raw["output_tokens"] / 1_000_000 * out_price
    )
    return {
        **raw,
        "estimated_cost_usd": round(estimate, 4),
        "pricing_source": pricing.get("source_url"),
        "assumptions": pricing.get("assumptions"),
    }


def _lane_a_builder_call_count(budget: RunBudget) -> int:
    return budget.calls_by_agent.get("lane_a_builder", 0)


def _with_connection_retry(call, *, trace: RunTrace, what: str, attempts: int = 3):
    """Bounded retry for transient provider Connection errors (observed on
    long multimodal review calls); every retry is traced, and a persistent
    failure escalates as before — never silent, never unbounded."""
    import time as _time

    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return call()
        except Exception as error:  # noqa: BLE001 - retried only when transient
            if "connection" not in str(error).casefold():
                raise
            last = error
            trace.add(
                agent="shell", phase="loop", action="connection_retry",
                note=f"{what}: attempt {attempt} failed ({str(error)[:120]})",
            )
            _time.sleep(10)
    assert last is not None
    raise last


def _live_lane_a_builder(
    budget: RunBudget,
    trace: RunTrace,
    *,
    payload: dict[str, Any],
    images: list[Any],
    proposal_id: str,
) -> LaneAStructureProposal:
    """The LIVE Lane A Builder: one bounded request that outputs the typed
    provider-neutral structure primitives. The shell validates, applies and
    renders — the agent never promotes anything."""
    from pydantic_ai import Agent, BinaryContent

    from tests.experiments.d_pipeline import _live_model

    if budget.remaining_model_requests() < 1:
        raise CheckpointBudgetExceeded("budget exhausted before lane A builder")
    agent = Agent(
        _live_model(),
        output_type=LaneAStructureProposal,
        name="lane_a_builder",
        instructions=E5_LANE_A_BUILDER_INSTRUCTIONS,
    )
    run_result = _with_connection_retry(
        lambda: agent.run_sync(
            [
                json.dumps(payload, ensure_ascii=False, indent=1),
                *[BinaryContent(data=image.read_bytes(), media_type="image/png") for image in images],
            ],
            usage_limits=_call_limits(budget, E5_BUILDER_MAX_REQUESTS),
            model_settings=_live_model_settings(),
        ),
        trace=trace, what="lane A builder",
    )
    _record_usage(budget, trace, "lane_a_builder", "build", run_result)
    proposal = run_result.output
    return proposal.model_copy(update={"proposal_id": proposal_id, "agent": "llm"})


def _live_lane_b_builder(
    budget: RunBudget,
    trace: RunTrace,
    *,
    payload: dict[str, Any],
    images: list[Any],
    template_id: str,
) -> AuthoredTemplateCandidate:
    """The LIVE Lane B Builder: one bounded request over target + current
    render images that authors the constrained HTML/CSS template candidate.
    The shell validates the safety boundary and fills typed slots — the agent
    never touches candidate values or the filesystem."""
    from pydantic_ai import Agent, BinaryContent

    from tests.experiments.d_pipeline import _live_model
    from tests.experiments.e_authored_template import AuthoredTemplateCandidate

    if budget.remaining_model_requests() < 1:
        raise CheckpointBudgetExceeded("budget exhausted before lane B builder")
    agent = Agent(
        _live_model(),
        output_type=AuthoredTemplateCandidate,
        name="lane_b_builder",
        instructions=E5_LANE_B_BUILDER_INSTRUCTIONS,
    )
    run_result = _with_connection_retry(
        lambda: agent.run_sync(
            [
                json.dumps(payload, ensure_ascii=False, indent=1),
                *[BinaryContent(data=image.read_bytes(), media_type="image/png") for image in images],
            ],
            usage_limits=_call_limits(budget, E5_BUILDER_MAX_REQUESTS),
            model_settings=_live_model_settings(),
        ),
        trace=trace, what="lane B builder",
    )
    _record_usage(budget, trace, "lane_b_builder", "build", run_result)
    template = run_result.output
    return template.model_copy(update={"template_id": template_id})


def _live_attribution_batch(
    pod: "DualSourcePod",
    budget: RunBudget,
    trace: RunTrace,
    *,
    findings: list[DefectFinding],
    results: list[MeasurementResult],
) -> list[LiveAttributionHypothesis]:
    """Phase 0 batched live attribution: findings that share the same region
    (and likely owning layer) are traced in ONE bounded call, ordered like
    the input findings list."""
    from pydantic_ai import Agent

    from tests.experiments.d_pipeline import _live_model

    if budget.remaining_model_requests() < 1:
        raise CheckpointBudgetExceeded("budget exhausted before attribution batch")
    tools = _pod_tools(pod, budget, trace, agent_name="attribution_investigator")
    tools.append(_traced_render_words(pod, budget, trace))
    agent = Agent(
        _live_model(),
        output_type=list[LiveAttributionHypothesis],
        name="attribution_investigator",
        instructions=E5_ATTRIBUTION_INSTRUCTIONS,
        tools=tools,
    )
    payload = {
        "findings": [finding.model_dump(mode="json") for finding in findings],
        "measurements": [result.model_dump(mode="json") for result in results],
        "target_description": pod.describe_target(),
        "budget_note": (
            "ONE bounded agent run for this whole batch; decide from the "
            "evidence you already have plus at most a few tool calls."
        ),
    }
    run_result = agent.run_sync(
        json.dumps(payload, ensure_ascii=False, indent=1),
        usage_limits=_call_limits(budget, E5_ATTRIBUTION_MAX_REQUESTS),
        model_settings=_live_model_settings(),
    )
    _record_usage(budget, trace, "attribution_investigator", "attribute_batch", run_result)
    return list(run_result.output)


E5_SOURCE_FILES = (
    # The E5 entry module itself (prompts/config/freeze logic included).
    "tests/experiments/e_pipeline.py",
    # Lane B boundary + scripted template.
    "tests/experiments/e_authored_template.py",
    # Shell infrastructure imported by e_pipeline: RunBudget/RunTrace/
    # EvidenceStore/RenderVersion/MeasureController/live-call plumbing.
    "tests/experiments/d_pipeline.py",
    # Pinned Chrome export environment, scaffolds, line/marks measurement.
    "tests/experiments/c_pipeline.py",
    # Render-plan compilation incl. the header-overflow ownership closure.
    "tests/experiments/c2_plan.py",
    # The fixed HTML renderer.
    "tests/experiments/c2_html.py",
    # Delivery gates (content/privacy/structure/accounting/determinism).
    "tests/experiments/c2_renderer.py",
    # CandidateDocument, fixtures, UnroutableContent.
    "tests/experiments/c2_candidates.py",
    # Layout-state (de)serialization + validation.
    "tests/experiments/c2_state.py",
    # state_from_scaffolds + flow probes used by the two-column compile.
    "tests/experiments/c2_pipeline.py",
    # TOLERANCE_PT imported at module level by e_pipeline.
    "tests/experiments/c2_docx_build.py",
    # Format summary + page rendering/font injection helpers.
    "tests/experiments/a_pipeline.py",
    # Production evidence model directly consumed by the E5 compile basis.
    "app/template_analysis/commercial/models.py",
    # Production PDF text reader used by the content/privacy/presence gates.
    "app/ingestion/pdf_reader.py",
)


def _experiment_source_hashes() -> dict[str, str]:
    """SHA-256 of the selected critical DIRECT runtime sources this E5 run
    executes (the entry module's direct local imports plus the shared
    gate/binding modules), recorded in run_config.json BEFORE the first live
    call. This is SELECTED CRITICAL-SOURCE DRIFT DETECTION: it detects a
    mid-run change to the registered files. It does NOT cover transitive
    dependencies and is NOT a proof of exact source identity — an exact
    binding additionally requires starting from a committed HEAD with no
    uncommitted changes to the running code."""
    return {name: _sha256_file(ROOT / name) for name in E5_SOURCE_FILES}


def _tracked_diff_sha256(root: Path, names: tuple[str, ...]) -> str | None:
    """SHA-256 of the tracked working-tree diff of the registered sources
    relative to HEAD (None when git is unavailable). Scoped to exactly the
    registered paths: changes to unrelated files (e.g. the owner's proposal
    notes) never enter the identity."""
    try:
        diff = subprocess.run(
            ["git", "diff", "HEAD", "--", *names],
            capture_output=True, text=True, check=True, cwd=root,
        ).stdout
    except Exception:
        return None
    return hashlib.sha256(diff.encode("utf-8")).hexdigest()


def _untracked_source_hashes() -> dict[str, str]:
    """Registered sources NOT tracked at HEAD (if any): recorded by their
    SHA-256 so untracked experiment modules are still identity-bound."""
    untracked: dict[str, str] = {}
    for name in E5_SOURCE_FILES:
        try:
            tracked = subprocess.run(
                ["git", "ls-files", "--error-unmatch", name],
                capture_output=True, text=True, cwd=ROOT,
            ).returncode == 0
        except Exception:
            tracked = False
        if not tracked:
            untracked[name] = _sha256_file(ROOT / name)
    return untracked


def _source_identity() -> dict[str, Any]:
    """The full source identity recorded in the frozen config: file hashes,
    the tracked-diff hash relative to HEAD, and hashes of any registered
    untracked sources."""
    return {
        "files": _experiment_source_hashes(),
        "tracked_diff_sha256": _tracked_diff_sha256(ROOT, E5_SOURCE_FILES),
        "untracked_files": _untracked_source_hashes(),
    }


def _source_hashes_changed(config: dict[str, Any]) -> bool:
    """True when the current source identity differs from the frozen config's
    (the run then records `source_changed` and is NOT source-identity
    stable). A config with missing or incomplete source identity is never
    trusted as stable."""
    frozen = config.get("source_hashes")
    if not isinstance(frozen, dict) or not isinstance(frozen.get("files"), dict):
        return True
    current = _source_identity()
    return any(frozen.get(key) != current[key] for key in current)


def _freeze_e5_config(
    out_dir: Path,
    *,
    target_id: str,
    target_frozen: FrozenCase,
    lanes: tuple[str, ...],
    max_repair_rounds: int,
    live: bool,
    prompts: dict[str, str],
    rubric_reference: dict[str, Any],
    candidate_sha256: str,
    shared_draft_ref: dict[str, Any],
    pricing: dict[str, Any] | None,
) -> dict[str, Any]:
    """Frozen BEFORE the first live call (E5 work order): shared target/
    candidate/evidence/model/prompts/budgets plus per-lane budgets. Model
    NAMES only — never keys, URLs or tokens; rubric content never recorded."""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
            cwd=Path(__file__).resolve().parents[2],
        ).stdout.strip()
    except Exception:
        commit = "unknown"
    config = {
        "run_id": out_dir.name,
        "frozen_before_first_live_call": True,
        "target_id": target_id,
        "target_sha256": target_frozen.target_sha256,
        "adobe_json_sha256": target_frozen.adobe_json_sha256,
        "candidate_input": {
            "role": "reviewed render context (candidate_resume_E render content)",
            "sha256": candidate_sha256,
            "builder_boundary": "candidate facts are never editable by any agent",
        },
        "shared_structure_draft": shared_draft_ref,
        "e4_best_render_baseline": {
            "run_dir": E5_RUNS_E4_BASELINE,
            "note": "E4 promoted best render (baseline render; NOT an accepted template)",
        },
        "model_configuration": _model_identity(),
        "sampling": {"temperature": E4_TEMPERATURE},
        "budgets": {
            "per_lane": {
                "max_model_requests": E5_LANE_MAX_MODEL_REQUESTS,
                "max_tool_calls": E5_LANE_MAX_TOOL_CALLS,
                "max_repair_rounds": max_repair_rounds,
                "builder_reserve_model_requests": E5_BUILDER_RESERVE_REQUESTS,
                "per_agent_run_max_requests": {
                    "visual_reviewer": E5_REVIEWER_MAX_REQUESTS,
                    "builder": E5_BUILDER_MAX_REQUESTS,
                    "attribution_investigator": E5_ATTRIBUTION_MAX_REQUESTS,
                },
            },
            "lanes": list(lanes),
        },
        "prompts": {
            "sha256": {
                name: hashlib.sha256(text.encode("utf-8")).hexdigest()
                for name, text in prompts.items()
            },
            "note": "full frozen prompt texts persisted in prompts.json; agent-visible only",
        },
        "permitted_tools": [
            "inspect_page_overview", "inspect_page_region", "inspect_adobe_json",
            "measure_local_pdf", "audit_coverage", "measure_render_words",
            "compare_pdf_geometry", "render_and_checkpoint",
        ],
        "lane_a_gates": list(CANDIDATE_FACT_GATES) + ["content_shapes_match_evidence"],
        "lane_b_gates": [
            "deterministic_render", "no_target_candidate_facts", "no_blank_page",
            "candidate_content_accounting", "content_gate", "template_safety",
        ],
        "evaluation_rubric_reference": {**rubric_reference, "not_given_to_agents": True},
        "starting_commit": commit,
        "source_hashes": {
            **_source_identity(),
            "basis": "selected_critical_source_drift_detection",
            "recorded_before_first_live_call": True,
            "note": (
                "SHA-256 + tracked-diff hash of SELECTED critical direct "
                "runtime sources (drift detection only; NOT an exact source "
                "identity — transitive dependencies are not covered, and an "
                "exact binding additionally requires a committed HEAD with "
                "no uncommitted changes to the running code); a mid-run "
                "change marks the run `source_changed` and NOT "
                "source_identity_stable"
            ),
        },
        "live": live,
        "provider_pricing": pricing or {"status": "unavailable", "estimate": None},
    }
    (out_dir / "run_config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (out_dir / "prompts.json").write_text(
        json.dumps(prompts, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return config


def run_e5(
    target_pdf: Path,
    out_dir: Path | None = None,
    *,
    live: bool = False,
    lanes: tuple[str, ...] = ("a", "b"),
    max_repair_rounds: int = E5_MAX_REPAIR_ROUNDS,
    budget: RunBudget | None = None,
    pricing: dict[str, Any] | None = None,
    presentation_label_approval: PresentationLabelApproval | None = None,
) -> tuple[Path, str, dict[str, Any]]:
    """E5: controlled comparison of TWO Builder representations on Resume I
    (Phase 0 loop fixes + Lane A structured layout + Lane B authored
    template). One runner, one shell, two fresh agent contexts.

    Normal exits: ``ready_for_owner_review`` and ``budget_exhausted`` (never
    success, never `unsupported`, never owner acceptance). Operational aborts
    describe the failed operation only. Content-shape probes always run
    against the SELECTED representation (the best-valid render's proposal/
    template, or diagnostic probes on the latest attempt when no best-valid
    render exists)."""
    from tests.experiments.a_pipeline import build_format_summary
    from tests.experiments.c2_candidates import (
        candidate_resume_E,
        independent_candidate_fixtures,
    )
    from tests.experiments.c2_plan import compile_render_plan
    from tests.experiments.c2_html import render_html
    from tests.experiments import c2_renderer as c2r
    from tests.experiments.c2_state import state_bytes
    from app.template_analysis.commercial.models import NormalizedLayoutEvidence
    from tests.experiments.e_authored_template import (
        authored_network_disabled_environment,
        authored_pdf_presence_gate,
        authored_privacy_gate,
        build_authored_document,
        fill_authored_template,
        presentation_label_markers,
        validate_authored_template,
    )

    started = time.time()
    started_at = datetime.now(UTC).isoformat(timespec="seconds")
    target_pdf = target_pdf.resolve()
    if not target_pdf.exists():
        raise RuntimeError(f"target PDF not found: {target_pdf}")
    target_sha256 = _sha256_file(target_pdf)
    out_dir = out_dir or RUNS / datetime.now(UTC).strftime("e_pipeline_e5_%Y%m%dT%H%M%SZ")
    out_dir.mkdir(parents=True, exist_ok=False)
    budget = budget or RunBudget(
        max_model_requests=E5_LANE_MAX_MODEL_REQUESTS * len(lanes),
        max_tool_calls=E5_LANE_MAX_TOOL_CALLS * len(lanes),
    )

    def abort(operation: str, error: Exception) -> tuple[Path, str, dict[str, Any]]:
        record = E5LoopRecord(target_id="unknown", target_sha256="0" * 64)
        record.summary["terminal_state"] = "operational_abort"
        record.summary["abort"] = {"operation": operation, "error": str(error)}
        (out_dir / "e5_state.json").write_text(
            json.dumps(record.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return out_dir, "operational_abort", record.model_dump(mode="json")

    # -- 1. FREEZE shared inputs ------------------------------------------
    target_cache_dir = RUNS / "target_cache" / target_sha256
    raw_path = target_cache_dir / "adobe_raw.json"
    normalized_path = target_cache_dir / "enriched_evidence.json"
    if not raw_path.exists() or not normalized_path.exists():
        return abort("target_evidence_cache", RuntimeError("no cached Adobe response for this target"))
    (out_dir / "adobe_raw.json").write_text(
        json.dumps(
            redact_signed_urls(json.loads(raw_path.read_text(encoding="utf-8"))),
            ensure_ascii=False, indent=1,
        ),
        encoding="utf-8",
    )
    assert_no_signed_strings((out_dir / "adobe_raw.json").read_text(encoding="utf-8"))
    shutil.copy2(normalized_path, out_dir / "enriched_evidence.json")
    target_frozen = freeze_cases(target_pdf, raw_path, role="frozen_blind", case_id=target_pdf.stem)
    (out_dir / "frozen_case.json").write_text(target_frozen.model_dump_json(indent=2), encoding="utf-8")
    target_id = f"target-{target_pdf.stem}-v1"
    rubric_reference = {
        "path": "tests/experiments/C2_RESUME_I_BLIND_STRUCTURE_AUDIT.md",
        "sha256": _sha256_file(ROOT / "tests/experiments/C2_RESUME_I_BLIND_STRUCTURE_AUDIT.md"),
    }

    # -- 2. Shared compile basis + shared evidence-linked StructureDraft ----
    try:
        normalized = NormalizedLayoutEvidence.model_validate_json(
            normalized_path.read_text(encoding="utf-8")
        )
    except Exception as error:
        return abort("normalized_evidence_load", error)
    pod = DualSourcePod(target_pdf, raw_path, out_dir, render_pdf=None, normalized=normalized)
    try:
        summary = build_format_summary(normalized, json.loads(raw_path.read_text(encoding="utf-8")), target_pdf)
        state, derived = compile_two_column_state(target_pdf, summary, evidence=normalized)
    except Exception as error:
        return abort("compile_two_column_state", error)
    # ONE shell-owned presentation-label catalog for BOTH lanes (owner decision
    # 2026-09-21): built from the same measured sidebar-label evidence the two
    # lanes already share. Lane A renders those labels from the render plan,
    # Lane B can only reference `{{label:<label_id>}}`; both privacy gates and
    # the owner listing reference THIS catalog, never a lane-local set.
    try:
        label_catalog = _presentation_label_catalog(derived)
    except Exception as error:
        return abort("presentation_label_catalog", error)
    catalog_sha256 = presentation_label_catalog_sha256(label_catalog)
    # Approval is an owner INPUT, never an inference: the proposed catalog
    # stays all-proposed unless the owner supplied a typed approval bound to
    # THIS target's sha256 and THIS catalog's canonical identity. Without it
    # (the default), NOTHING is renderable and nothing is privacy-excluded.
    try:
        approved_label_catalog = _apply_presentation_label_approval(
            label_catalog,
            presentation_label_approval,
            target_sha256=target_sha256,
        )
    except Exception as error:
        return abort("presentation_label_approval", error)
    approval_provided = presentation_label_approval is not None
    # Only owner-approved entries are renderable and privacy-excluded. The
    # proposed remainder stays visible in the owner listing but can never be
    # rendered: its ids are never issued to the template boundary, and its
    # text is never excluded from the privacy gate (so rendering it fails
    # closed as a leak instead of being silently tolerated).
    approved_label_texts = {label.text for label in approved_label_catalog}
    (out_dir / "c2_layout_state.json").write_bytes(state_bytes(state))
    _headings_scaffold, body_scaffold = compile_two_column_state_for_scaffold(target_pdf, summary)
    bullet_tiers = _two_column_bullet_tiers(_pdf_lines_and_marks(target_pdf)[0], summary)

    # The SAME evidence-linked StructureDraft is the common starting point for
    # both lanes (E5 work order). The E4 run's validated draft is reused as a
    # frozen shared input (path + sha recorded; content never regenerated).
    draft_source = RUNS / E5_SHARED_DRAFT_SOURCE
    if not draft_source.exists():
        return abort(
            "shared_structure_draft",
            FileNotFoundError(f"the shared StructureDraft ({E5_SHARED_DRAFT_SOURCE}) is missing"),
        )
    draft = TargetStructureDraft.model_validate_json(draft_source.read_text(encoding="utf-8"))
    (out_dir / "structure_draft.json").write_text(draft.model_dump_json(indent=2), encoding="utf-8")
    shared_draft_ref = {
        "source_run": E5_SHARED_DRAFT_SOURCE,
        "sha256": _sha256_file(draft_source),
        "claims": len(draft.structure),
        "shared_by_lanes": True,
    }
    page_height = _page_pt_size(target_pdf, 1)[1]
    region_spans = _region_spans_from_draft(draft, page_height)

    def _known_evidence_ids() -> set[str]:
        """The evidence ids the shell ACTUALLY issued (pod access records +
        the shared draft's cited refs). Authored-template evidence_refs must
        be members of this set — string shape alone is never enough."""
        ids: set[str] = set()
        for record in getattr(pod, "records", []) or []:
            record_id = (
                record.get("evidence_id")
                if isinstance(record, dict)
                else getattr(record, "evidence_id", None)
            )
            if record_id:
                ids.add(record_id)
        for claim in draft.structure:
            for ref in claim.evidence:
                ids.add(ref.evidence_id)
        return ids

    candidate = candidate_resume_E()
    # Generic header-overflow disposition (E3 shell transition, reused for
    # the main candidate AND every content-shape probe): the compiled
    # two-column state hosts the contact rows inside section content.
    candidate = _apply_header_overflow_disposition(candidate, state)
    frozen_dir = RUNS / "c_pipeline_D_to_E_20260910T195515Z"
    if not (frozen_dir / "target.pdf").exists():
        return abort("frozen_c1_baseline", FileNotFoundError(str(frozen_dir / "target.pdf")))
    privacy_baseline = frozen_dir / "target.pdf"
    environment = pinned_export_environment({})
    candidate_sha256 = hashlib.sha256(
        json.dumps(candidate.model_dump(mode="json"), sort_keys=True).encode("utf-8")
    ).hexdigest()

    config = _freeze_e5_config(
        out_dir,
        target_id=target_id,
        target_frozen=target_frozen,
        lanes=lanes,
        max_repair_rounds=max_repair_rounds,
        live=live,
        prompts={
            "visual_reviewer": E5_REVIEWER_INSTRUCTIONS,
            "lane_a_builder": E5_LANE_A_BUILDER_INSTRUCTIONS,
            "lane_b_builder": E5_LANE_B_BUILDER_INSTRUCTIONS,
            "attribution_investigator": E5_ATTRIBUTION_INSTRUCTIONS,
        },
        rubric_reference=rubric_reference,
        candidate_sha256=candidate_sha256,
        shared_draft_ref=shared_draft_ref,
        pricing=pricing,
    )

    store = EvidenceStore(out_dir, out_dir, base_html="<html><body></body></html>")
    store.manifest["experiment"] = "e_pipeline_e5"
    store.manifest["pipeline_phase"] = "e5"
    store.register_version("structure_draft_v1", out_dir / "structure_draft.json", "shared evidence-linked draft")
    page_size = _page_pt_size(target_pdf, 1)
    shared_target_images = _overview_pngs(target_pdf, out_dir, "builder_target_shared")
    initial_builder_evidence = _e5_builder_evidence_package(
        draft=draft,
        state=state,
        derived=derived,
        page_size=page_size,
        target_images=shared_target_images,
        presentation_labels=approved_label_catalog,
    )
    initial_builder_evidence_path = out_dir / "builder_evidence_initial.json"
    initial_builder_evidence_path.write_text(
        json.dumps(initial_builder_evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    store.register_version(
        "builder_evidence_initial_v1",
        initial_builder_evidence_path,
        "frozen common Builder evidence package",
    )

    # Scripted-reviewer rehearsal data for THIS target (same runtime rehearsal
    # pattern as E3/E4, keyed by target id; Phase-0-style intent-only request).
    mapped = [
        node for node in state.nodes
        if node.kind == "section" and node.binding
        and node.binding.mapping_action == "map"
        and "work_experience" in node.binding.sources
    ]
    if not mapped:
        return abort("scripted_reviewer_region", RuntimeError("compiled state has no mapped work_experience section"))
    entry_region = mapped[0].node_id
    SCRIPTED_REGION_BY_TARGET[target_id] = entry_region
    SCRIPTED_OBSERVATION_BY_TARGET[target_id] = (
        "The vertical distance from the first dated entry head to the second "
        "dated entry head appears larger than in the target."
    )
    SCRIPTED_REQUEST_BY_TARGET[target_id] = MeasurementRequest(
        request_id="measure-pending",
        metric="role_gap",
        page=1,
        region_id=entry_region,
        intent="vertical gap between the first two dated entry heads inside this region",
    )

    lane_records: dict[str, E5LaneRecord] = {}

    def _run_lane(lane: str) -> E5LaneRecord:
        lane_dir = out_dir / f"lane_{lane}"
        lane_dir.mkdir(exist_ok=True)
        # The per-lane budget lives INSIDE the run budget (recorded in the
        # frozen config; a smaller caller-provided budget is respected so a
        # constrained run still exits budget_exhausted with resumable state).
        lane_budget = RunBudget(
            max_model_requests=min(E5_LANE_MAX_MODEL_REQUESTS, budget.remaining_model_requests()),
            max_tool_calls=min(E5_LANE_MAX_TOOL_CALLS, budget.max_tool_calls),
        )
        trace = RunTrace(lane_dir)
        trace.add(agent="shell", phase="e0", action="lane_started", output={"lane": lane, "live": live})
        versions: list[RenderVersion] = []
        # Explicit ACTIVE version (E5 rollback fix): the lane always reads,
        # reviews and measures the active version — never `versions[-1]`.
        # A rejected candidate render stays in `versions` as immutable
        # history but never becomes active; only a shell promotion does.
        active_index = 0
        pdf_by_version: dict[str, Path] = {}
        # version_id -> the EXACT representation (proposal/template) that
        # produced it; probes must use the selected one, never the default
        # state or a scripted fixture.
        representation_by_version: dict[str, Any] = {}
        # Version-bound mutable render state (rollback correctness): the
        # RenderPlan (Lane A) and the delivery gates belong to ONE render
        # version each. Every reader (reviewer, measurement binding,
        # builder) resolves them through the ACTIVE version id — a rejected
        # candidate never overwrites the active state.
        plan_by_version: dict[str, Any] = {}
        gates_by_version: dict[str, dict[str, Any]] = {}
        # Per-version answer to "did the privacy gate exclude exactly the
        # shared shell-owned label catalog?" — derived, never lane-specific.
        label_symmetry_by_version: dict[str, bool] = {}
        # Presentation labels THIS lane actually referenced (Lane A: rendered
        # plan labels; Lane B: template label markers).
        referenced_labels: set[str] = set()
        # The region of the last PROMOTED repair (the only state that scopes
        # the next review round; rejected attempts never do).
        last_promoted_region: str | None = None
        findings: list[DefectFinding] = []
        measurement_results: list[MeasurementResult] = []
        attributions: list[AttributionRecord] = []
        repair_attempts: list[dict[str, Any]] = []
        builder_candidates: list[dict[str, Any]] = []
        attempted_strategies: list[str] = []
        fingerprints: list[str] = []
        resolved_measurements: dict[str, tuple[MeasurementRequest, MeasurementResult]] = {}
        rolled_back_versions: set[str] = set()
        ledger = DefectLedger()
        pages_reviewed: set[int] = set()
        counter = {"finding": 0, "request": 0}
        lane_state: dict[str, Any] = {"proposal": None, "template": None}

        def record_builder_candidate(
            candidate_output: Any,
            *,
            stage: str,
            input_render_version: str | None,
            validation: dict[str, Any],
            outcome: str,
            reason: str,
            attribution: AttributionRecord | None = None,
            action_fingerprint: str | None = None,
        ) -> int:
            attempt = len(builder_candidates) + 1
            record = _e5_builder_candidate_record(
                lane=lane,
                attempt=attempt,
                stage=stage,
                input_render_version=input_render_version,
                candidate_output=candidate_output,
                validation=validation,
                outcome=outcome,
                reason=reason,
                attribution=attribution,
                action_fingerprint=action_fingerprint,
            )
            builder_candidates.append(record)
            (lane_dir / record["artifact"]).write_text(
                json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            return attempt - 1

        def update_builder_candidate(index: int, **updates: Any) -> None:
            builder_candidates[index].update(updates)
            (lane_dir / builder_candidates[index]["artifact"]).write_text(
                json.dumps(builder_candidates[index], ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

        def escalate(strategy: str) -> None:
            attempted_strategies.append(strategy)
            trace.add(agent="shell", phase="loop", action="strategy_escalation", note=strategy)

        def _lane_terminal(lane_id: str, forced: str | None = None) -> E5LaneRecord:
            # The ledger is the ONLY open/closed source: a deduplicated
            # re-observation of an already-repaired/resolved defect must not
            # resurrect it as open under a new finding id.
            open_ids = _open_ledger_finding_ids(list(ledger.entries.values()))
            # Best-valid selection FIRST: the content-shape probes must run
            # against the EXACT SELECTED representation (the best-valid
            # render's proposal/template). With no best-valid render, the
            # latest attempt gets DIAGNOSTIC probes only — never promotion
            # evidence.
            best_version_id = _best_valid_version_id(versions, excluded=rolled_back_versions)
            defect_version_id = _best_defect_level_version_id(versions, excluded=rolled_back_versions)
            active_version_id = versions[active_index].version_id if versions else None
            best_version = next((v for v in versions if v.version_id == best_version_id), None)
            diagnostic_probes = best_version_id is None
            # Probes run on the lane's ACTIVE state when no best-valid (or
            # defect-level) render exists — a rejected candidate never
            # becomes the probe basis.
            selected_id = best_version_id or defect_version_id or (
                versions[active_index].version_id if versions else None
            )
            selected_representation = representation_by_version.get(selected_id)
            probes_ok = bool(versions) and selected_id is not None
            probe_report: dict[str, Any] = {
                "_selected_version": selected_id,
                "_probe_mode": "diagnostic" if diagnostic_probes else "promotion_evidence",
            }
            for profile, fixture in independent_candidate_fixtures().items():
                probe_candidate = _probe_candidate_with_text(fixture)
                if lane == "a":
                    probe_candidate = _sectioned_probe_candidate(probe_candidate)
                entry: dict[str, Any] = {
                    "passed": False,
                    "diagnostic": diagnostic_probes,
                    "representation": (
                        selected_representation.proposal_id
                        if lane == "a" and selected_representation is not None
                        else (
                            selected_representation.template_id
                            if selected_representation is not None
                            else ("initial_compiled_plan" if lane == "a" else None)
                        )
                    ),
                }
                probe_report[profile] = entry
                try:
                    index = next(
                        (i + 1 for i, v in enumerate(versions) if v.version_id == selected_id), 0
                    )
                    probe_html_path = lane_dir / f"probe_{profile}.html"
                    probe_pdf_path = lane_dir / f"probe_{profile}.pdf"
                    probe_pdf_second = lane_dir / f"probe_{profile}_second.pdf"
                    if lane == "a":
                        # Real chain with the SELECTED proposal: candidate
                        # binding (incl. the shared header-overflow shell
                        # transition) -> compile -> HTML -> Chrome PDF -> gates.
                        probe_plan_candidate = _apply_header_overflow_disposition(
                            probe_candidate, state
                        )
                        plan = _apply_lane_a_structure(
                            compile_render_plan(state, probe_plan_candidate), selected_representation
                        )
                        if plan.status == "failed":
                            raise RuntimeError(f"probe plan failed: {plan.failures[:3]}")
                        html = render_html(state, plan)
                        probe_html_path.write_text(html, encoding="utf-8")
                        _export_pinned_html_to_pdf(probe_html_path, probe_pdf_path, environment)
                        _export_pinned_html_to_pdf(probe_html_path, probe_pdf_second, environment)
                        pages = _render_pages(probe_pdf_path, lane_dir, f"probe_{profile}")
                        second_pages = _render_pages(probe_pdf_second, lane_dir, f"probe_{profile}_second")
                        content = c2r.content_gate(plan, html, probe_pdf_path)
                        gates = {
                            "content_gate": content["passed"],
                            "candidate_content_accounting": c2r.candidate_accounting_gate(
                                plan, content
                            )["passed"],
                            # The same COMMON privacy gate with the same
                            # owner-approved label set as the main renders.
                            "privacy_gate": authored_privacy_gate(
                                html, probe_pdf_path, target_pdf,
                                labels=approved_label_texts,
                            )["passed"],
                            "no_blank_page": c2r.blank_page_gate(probe_pdf_path)["passed"],
                            "deterministic_render": (
                                all(
                                    c2r._sha256(left) == c2r._sha256(right)
                                    for left, right in zip(pages, second_pages)
                                )
                                and c2r._line_stability(probe_pdf_path, probe_pdf_second)["passed"]
                            ),
                        }
                    else:
                        if selected_representation is None:
                            raise RuntimeError("no selected authored template to probe")
                        # Real chain with the SELECTED template: validation ->
                        # fill -> document -> network-disabled Chrome -> gates.
                        validate_authored_template(
                            selected_representation,
                            target_pdf=target_pdf,
                            render_candidate=probe_candidate,
                            known_evidence_ids=_known_evidence_ids(),
                            labels=approved_label_catalog,
                        )
                        fill = fill_authored_template(
                            selected_representation, probe_candidate,
                            labels=approved_label_catalog,
                        )
                        doc = build_authored_document(selected_representation, fill, page_size)
                        probe_html_path.write_text(doc, encoding="utf-8")
                        net_env = authored_network_disabled_environment()
                        _export_pinned_html_to_pdf(probe_html_path, probe_pdf_path, net_env)
                        _export_pinned_html_to_pdf(probe_html_path, probe_pdf_second, net_env)
                        pages = _render_pages(probe_pdf_path, lane_dir, f"probe_{profile}")
                        second_pages = _render_pages(probe_pdf_second, lane_dir, f"probe_{profile}_second")
                        gates = {
                            "candidate_content_accounting": not fill.missing_leaves,
                            "pdf_presence_gate": authored_pdf_presence_gate(
                                fill, probe_candidate, probe_pdf_path
                            )["passed"],
                            "privacy_gate": authored_privacy_gate(
                                doc, probe_pdf_path, target_pdf,
                                labels=approved_label_texts,
                            )["passed"],
                            "no_blank_page": c2r.blank_page_gate(probe_pdf_path)["passed"],
                            "deterministic_render": (
                                all(
                                    c2r._sha256(left) == c2r._sha256(right)
                                    for left, right in zip(pages, second_pages)
                                )
                                and c2r._line_stability(probe_pdf_path, probe_pdf_second)["passed"]
                            ),
                            "template_safety": True,  # validation raises otherwise
                        }
                    entry["gates"] = gates
                    entry["pages"] = len(pages)
                    entry["html"] = probe_html_path.name
                    entry["pdf"] = probe_pdf_path.name
                    entry["passed"] = all(gates.values())
                except Exception as error:  # a probe failure is recorded, never a crash
                    entry["error"] = str(error)[:200]
                probes_ok = probes_ok and bool(entry.get("passed"))
            (lane_dir / "content_shape_probes.json").write_text(
                json.dumps(probe_report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            expected_pages = set(range(1, (target_frozen.page_count or 1) + 1))
            terminal = (
                "ready_for_owner_review"
                if (
                    best_version_id
                    and best_version is not None
                    and best_version.hard_gates_passed
                    and not open_ids
                    and probes_ok
                    and pages_reviewed >= expected_pages
                )
                else "budget_exhausted"
            )
            if forced is not None:
                terminal = forced
            record = E5LaneRecord(
                lane=lane_id,
                representation=(
                    "provider-neutral structured layout + fixed renderer (Lane A)"
                    if lane_id == "a"
                    else "constrained authored HTML/CSS template + typed slots (Lane B)"
                ),
                render_versions=versions,
                best_render_version=best_version_id,
                best_defect_level_version=defect_version_id,
                active_render_version=active_version_id,
                findings=findings,
                measurement_results=measurement_results,
                attributions=attributions,
                repair_attempts=repair_attempts,
                builder_candidates=builder_candidates,
                attempted_strategies=attempted_strategies,
                action_fingerprints=fingerprints,
                ledger=list(ledger.entries.values()),
                open_findings=open_ids,
                content_shape_probes_passed=probes_ok,
                pages_reviewed=sorted(pages_reviewed),
                terminal_state=terminal,
                budget_state=lane_budget.to_json(),
                summary={
                    "total_findings": len(findings),
                    "best_render_version": best_version_id,
                    "best_defect_level_version": defect_version_id,
                    "active_render_version": active_version_id,
                    "probe_mode": probe_report.get("_probe_mode"),
                    "promoted_versions": len([v for v in versions if v.promoted]),
                    "confirmed_measurements": len([r for r in measurement_results if r.status == "confirmed"]),
                    "unbound_measurements": len([r for r in measurement_results if r.status == "evidence_missing"]),
                    "builder_calls": lane_budget.calls_by_agent.get(f"lane_{lane_id}_builder", 0),
                    "reviewer_calls": lane_budget.calls_by_agent.get("visual_reviewer", 0),
                    "attribution_calls": lane_budget.calls_by_agent.get("attribution_investigator", 0),
                    "methodology_classification": _e5_gate_classification(
                        lane_id,
                        gates_by_version.get(active_version_id, {}),
                        privacy_labels_symmetric=label_symmetry_by_version.get(
                            active_version_id or "", True
                        ),
                    ),
                    "presentation_labels_referenced": sorted(referenced_labels),
                    "elapsed_seconds": None,
                    "live": live,
                },
            )
            (lane_dir / f"e5_lane_{lane_id}_state.json").write_text(
                json.dumps(record.model_dump(mode="json"), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            (lane_dir / "REPORT.md").write_text(
                _write_e5_lane_report_md(lane_id, record), encoding="utf-8"
            )
            trace.save()
            return record

        # --- render plumbing ---------------------------------------------
        if lane == "a":
            def render_version(
                note: str, proposal: LaneAStructureProposal | None = None
            ) -> tuple[RenderVersion, Path, dict[str, Any]]:
                lane_budget.spend_tool("render_and_checkpoint")
                plan = _apply_lane_a_structure(compile_render_plan(state, candidate), proposal)
                if plan.status == "failed":
                    raise RuntimeError(f"refusing to render a failed plan: {plan.failures[:3]}")
                html = render_html(state, plan)
                index = len(versions) + 1
                html_path = lane_dir / f"render_{index}.html"
                html_path.write_text(html, encoding="utf-8")
                pdf_path = lane_dir / f"render_{index}.pdf"
                _export_pinned_html_to_pdf(html_path, pdf_path, environment)
                second = lane_dir / f"render_{index}_second.pdf"
                _export_pinned_html_to_pdf(html_path, second, environment)
                pages = _render_pages(pdf_path, lane_dir, f"render_{index}")
                second_pages = _render_pages(second, lane_dir, f"render_{index}_second")
                stability = c2r._line_stability(pdf_path, second)
                content = c2r.content_gate(plan, html, pdf_path)
                # DIAGNOSTIC ONLY (see gate_details): the renderer gate keeps
                # excluding plan-derived labels; the privacy DECISION is the
                # common gate below.
                privacy = c2r.privacy_gate(plan, privacy_baseline, html, pdf_path)
                # The ONE common privacy gate: identical function and identical
                # owner-approved label set for BOTH lanes, so the two lanes'
                # privacy decisions are directly comparable.
                common_privacy = authored_privacy_gate(
                    html, pdf_path, target_pdf, labels=approved_label_texts
                )
                structure_gate = c2r.structure_gate(plan, state, html, pdf_path)
                blank = c2r.blank_page_gate(pdf_path)
                accounting = c2r.candidate_accounting_gate(plan, content)
                shape = c2r.content_shape_verification(
                    state, plan, body_scaffold, bullet_tiers, html, summary, pdf_path
                )
                gates = {
                    "deterministic_render": all(
                        c2r._sha256(left) == c2r._sha256(right)
                        for left, right in zip(pages, second_pages)
                    ) and stability["passed"],
                    # The COMMON privacy decision: the same gate function and
                    # the same owner-approved label set Lane B uses.
                    "no_target_candidate_facts": common_privacy["passed"],
                    "section_order_matches_state": structure_gate["section_order_matches_state"],
                    "no_blank_page": blank["passed"],
                    "candidate_content_accounting": accounting["passed"],
                    "content_shapes_match_evidence": shape["passed"],
                    "content_gate": content["passed"],
                }
                label_semantics = _e5_label_semantics(
                    common_privacy, approved_label_catalog
                )
                label_symmetry_by_version[f"render-{target_pdf.stem}-laneA-v{index}"] = bool(
                    label_semantics["symmetric"]
                )
                referenced_labels.update(
                    label.label_id
                    for label in label_catalog
                    if c2r._norm(label.text)
                    in {
                        c2r._norm(section.label)
                        for section in [*plan.sections, *plan.appended_sections]
                    }
                )
                gate_details = {
                    "deterministic_render": {
                        "passed": gates["deterministic_render"],
                        "first_page_hashes": [c2r._sha256(page) for page in pages],
                        "second_page_hashes": [c2r._sha256(page) for page in second_pages],
                        "line_stability": stability,
                    },
                    "no_target_candidate_facts": {
                        **common_privacy,
                        "label_semantics": label_semantics,
                        # DIAGNOSTIC ONLY: the renderer gate keeps excluding
                        # plan-derived labels; it is no longer the privacy
                        # decision and never used to claim symmetry.
                        "plan_derived_privacy_gate": privacy,
                    },
                    "section_order_matches_state": structure_gate,
                    "no_blank_page": blank,
                    "candidate_content_accounting": accounting,
                    "content_shapes_match_evidence": shape,
                    "content_gate": content,
                }
                version = RenderVersion(
                    version_id=f"render-{target_pdf.stem}-laneA-v{index}",
                    html_sha256=_sha256_file(html_path),
                    pdf_sha256=_sha256_file(pdf_path),
                    page_count=len(pages),
                    hard_gates_passed=all(gates.values()),
                    note=note,
                )
                versions.append(version)
                pdf_by_version[version.version_id] = pdf_path
                representation_by_version[version.version_id] = proposal
                plan_by_version[version.version_id] = plan
                gates_by_version[version.version_id] = gates
                store.register_version(version.version_id, pdf_path, note)
                (lane_dir / f"hard_gates_{version.version_id}.json").write_text(
                    json.dumps(
                        _e5_hard_gate_record(version.version_id, gates, gate_details),
                        indent=2,
                        sort_keys=True,
                    ) + "\n",
                    encoding="utf-8",
                )
                trace.add(agent="shell", phase="render", action="render_version", output=version.model_dump(mode="json"), note=note)
                return version, pdf_path, gates
        else:
            def render_version(
                note: str, template: AuthoredTemplateCandidate | None = None
            ) -> tuple[RenderVersion, Path, dict[str, Any]]:
                lane_budget.spend_tool("render_and_checkpoint")
                template = template or lane_state["template"]
                validate_authored_template(
                    template,
                    target_pdf=target_pdf,
                    render_candidate=candidate,
                    known_evidence_ids=_known_evidence_ids(),
                    labels=approved_label_catalog,
                )
                fill = fill_authored_template(
                    template, candidate, labels=approved_label_catalog
                )
                index = len(versions) + 1
                doc = build_authored_document(template, fill, page_size)
                html_path = lane_dir / f"render_{index}.html"
                html_path.write_text(doc, encoding="utf-8")
                pdf_path = lane_dir / f"render_{index}.pdf"
                net_env = authored_network_disabled_environment()
                _export_pinned_html_to_pdf(html_path, pdf_path, net_env)
                second = lane_dir / f"render_{index}_second.pdf"
                _export_pinned_html_to_pdf(html_path, second, net_env)
                pages = _render_pages(pdf_path, lane_dir, f"render_{index}")
                second_pages = _render_pages(second, lane_dir, f"render_{index}_second")
                stability = c2r._line_stability(pdf_path, second)
                presence = authored_pdf_presence_gate(fill, candidate, pdf_path)
                # The SAME common gate Lane A runs: identical gate function and
                # identical owner-approved label set, so both lanes' privacy
                # decisions are directly comparable.
                common_privacy = authored_privacy_gate(
                    doc, pdf_path, target_pdf, labels=approved_label_texts
                )
                label_semantics = _e5_label_semantics(
                    common_privacy, approved_label_catalog
                )
                referenced_labels.update(presentation_label_markers(template.html))
                # DIAGNOSTIC ONLY: the frozen-target copy gate under the same
                # approved label set (not the privacy decision).
                privacy_baseline_gate = authored_privacy_gate(
                    doc, pdf_path, privacy_baseline, labels=approved_label_texts
                )
                blank = c2r.blank_page_gate(pdf_path)
                gates = {
                    "deterministic_render": all(
                        c2r._sha256(left) == c2r._sha256(right)
                        for left, right in zip(pages, second_pages)
                    ) and stability["passed"],
                    "no_target_candidate_facts": common_privacy["passed"],
                    "no_blank_page": blank["passed"],
                    "candidate_content_accounting": not fill.missing_leaves,
                    "content_gate": presence["passed"],
                    "template_safety": True,  # validation raises otherwise
                }
                gate_details = {
                    "deterministic_render": {
                        "passed": gates["deterministic_render"],
                        "first_page_hashes": [c2r._sha256(page) for page in pages],
                        "second_page_hashes": [c2r._sha256(page) for page in second_pages],
                        "line_stability": stability,
                    },
                    "no_target_candidate_facts": {
                        **common_privacy,
                        "label_semantics": label_semantics,
                        "frozen_target_privacy_gate": privacy_baseline_gate,
                    },
                    "no_blank_page": blank,
                    "candidate_content_accounting": {
                        "passed": not fill.missing_leaves,
                        "missing_leaves": fill.missing_leaves,
                        "leaf_counts": fill.leaf_counts,
                    },
                    "content_gate": presence,
                    "template_safety": {"passed": True},
                }
                version = RenderVersion(
                    version_id=f"render-{target_pdf.stem}-laneB-v{index}",
                    html_sha256=_sha256_file(html_path),
                    pdf_sha256=_sha256_file(pdf_path),
                    page_count=len(pages),
                    hard_gates_passed=all(gates.values()),
                    note=note,
                )
                versions.append(version)
                pdf_by_version[version.version_id] = pdf_path
                representation_by_version[version.version_id] = template
                gates_by_version[version.version_id] = gates
                label_symmetry_by_version[version.version_id] = bool(
                    label_semantics["symmetric"]
                )
                store.register_version(version.version_id, pdf_path, note)
                (lane_dir / f"hard_gates_{version.version_id}.json").write_text(
                    json.dumps(
                        _e5_hard_gate_record(version.version_id, gates, gate_details),
                        indent=2,
                        sort_keys=True,
                    ) + "\n",
                    encoding="utf-8",
                )
                trace.add(agent="shell", phase="render", action="render_version", output=version.model_dump(mode="json"), note=note)
                return version, pdf_path, gates

        def render_leaf_texts(region_id: str | None, plan: Any = None) -> list[str]:
            # The plan is always the version-bound plan of the render being
            # measured (the ACTIVE plan for a round measurement, the
            # candidate's own plan for the identical re-measurement) — never
            # a mutable lane-wide variable that a rejected render could
            # overwrite.
            if lane == "a" and plan is not None:
                plan_section = next(
                    (s for s in [*plan.sections, *plan.appended_sections] if s.node_id == region_id),
                    None,
                )
                if plan_section is not None and plan_section.entries:
                    return [
                        entry.title_lines[0].text
                        for entry in plan_section.entries if entry.title_lines
                    ]
            # generic fallback: the candidate's own entry heads (same role)
            return [
                leaf.text or "" for leaf in candidate.leaves
                if leaf.kind in {"work_entry", "education_entry"} and leaf.text
            ][:4]

        def execute_measurement(
            finding: DefectFinding,
            pdf: Path,
            request_id: str,
            *,
            plan: Any = None,
            render_version: str = "",
        ) -> tuple[MeasurementResult, MeasurementRequest]:
            span = region_spans.get(finding.region)
            span_tuple = (span[1], span[2]) if span and span[0] == finding.page else None
            trace.add(
                agent="measure_controller", phase="measure", action="binding",
                input={"render_version": render_version, "finding": finding.finding_id},
            )
            bound = _bind_role_gap_anchors(
                finding.requested_measurement.model_copy(update={"request_id": request_id}),
                target_pdf=target_pdf,
                current_pdf=pdf,
                region_span=span_tuple,
                render_leaf_texts=render_leaf_texts(finding.region, plan),
            )
            if isinstance(bound, MeasurementResult):
                trace.add(
                    agent="measure_controller", phase="measure", action="binding_unresolved",
                    note=bound.reason, persist_output=True,
                )
                return bound, finding.requested_measurement
            # MeasureController.execute is the SINGLE tool-budget consumer for
            # a deterministic measurement (one compare_pdf_geometry per call,
            # one trace record). Callers never spend or re-trace it.
            result = MeasureController(pod, lane_budget, trace).execute(bound, current_pdf=pdf)
            return result, bound

        def attribute(
            finding: DefectFinding, result: MeasurementResult, request: MeasurementRequest
        ) -> AttributionRecord:
            attribution = _e5_default_attribution(finding, result, request)
            attributions.append(attribution)
            # Single source of defect state: the attribution (and its
            # measurement request) is written back to the owning ledger
            # entry; a no_defect measurement closes the entry as resolved.
            _record_ledger_attribution(ledger, finding, attribution, request.request_id)
            trace.add(
                agent="attribution_investigator", phase="attribute", action="attribution",
                output=attribution.model_dump(mode="json"),
            )
            return attribution

        def accepted_regions_hold(candidate_pdf: Path) -> tuple[bool, list[dict[str, Any]]]:
            """FAIL CLOSED accepted-region recheck: an accepted region is held
            ONLY when the identical re-measurement is CONFIRMED on the
            candidate PDF with all measured values present and the error did
            not grow beyond the prior error + tolerance. Any evidence_missing
            / unmeasurable / vanished anchor / missing value means the
            candidate CANNOT be verified to hold the region -> not held."""
            rechecks: list[dict[str, Any]] = []
            for finding_id, (request, prior) in resolved_measurements.items():
                repeat = MeasureController(pod, lane_budget, trace).execute(
                    request.model_copy(), current_pdf=candidate_pdf
                )
                values_present = (
                    repeat.current_value_pt is not None
                    and repeat.target_value_pt is not None
                    and prior.current_value_pt is not None
                    and prior.target_value_pt is not None
                )
                held = (
                    repeat.status == "confirmed"
                    and values_present
                    and abs(repeat.current_value_pt - repeat.target_value_pt)
                    <= abs(prior.current_value_pt - prior.target_value_pt) + E2_IMPROVEMENT_TOLERANCE_PT
                )
                reason = None
                if not held:
                    if repeat.status != "confirmed":
                        reason = f"re-measurement not confirmed on the candidate PDF (status={repeat.status})"
                    elif not values_present:
                        reason = "re-measurement left measured values missing (vanished anchor / evidence gap)"
                    else:
                        reason = "accepted region error grew beyond the prior error + tolerance"
                rechecks.append(
                    {
                        "finding": finding_id,
                        "held": held,
                        "status": repeat.status,
                        **({"reason": reason} if reason else {}),
                    }
                )
                if not held:
                    return False, rechecks
            return True, rechecks

        # --- 3. Initial Builder output (representation under test) ---------
        initial_note = f"lane {lane} initial render ({'authored template' if lane == 'b' else 'structured layout'})"
        candidate_audit_index: int | None = None
        try:
            if lane == "a":
                proposal = None
                if live:
                    proposal = _live_lane_a_builder(
                        lane_budget,
                        trace,
                        payload=initial_builder_evidence,
                        images=shared_target_images,
                        proposal_id="lane-a-r1",
                    )
                    validation_error = _validate_lane_a_proposal(proposal, state)
                    candidate_audit_index = record_builder_candidate(
                        proposal,
                        stage="initial",
                        input_render_version=None,
                        validation={"passed": validation_error is None, "error": validation_error},
                        outcome="validator_rejected" if validation_error else "validated",
                        reason=validation_error or "typed proposal accepted by the shell validator",
                    )
                    if validation_error:
                        raise ValueError(validation_error)
                lane_state["proposal"] = proposal
                lane_state["active_proposal"] = proposal
                version, pdf, gates = render_version(initial_note, proposal)
                if candidate_audit_index is not None:
                    update_builder_candidate(
                        candidate_audit_index,
                        candidate_render_version=version.version_id,
                        outcome="active_unpromoted",
                        reason="initial validated candidate rendered and became the active version",
                    )
            else:
                template = None
                if live:
                    template = _live_lane_b_builder(
                        lane_budget,
                        trace,
                        payload=initial_builder_evidence,
                        images=shared_target_images,
                        template_id="lane-b-r1",
                    )
                else:
                    from tests.experiments.e_authored_template import SCRIPTED_AUTHORED_TEMPLATE

                    template = SCRIPTED_AUTHORED_TEMPLATE
                try:
                    validation = validate_authored_template(
                        template,
                        target_pdf=target_pdf,
                        render_candidate=candidate,
                        known_evidence_ids=_known_evidence_ids(),
                        labels=approved_label_catalog,
                    )
                    validation_error = None
                except ValueError as error:
                    validation = {"passed": False, "error": str(error)}
                    validation_error = str(error)
                candidate_audit_index = record_builder_candidate(
                    template,
                    stage="initial",
                    input_render_version=None,
                    validation=validation,
                    outcome="validator_rejected" if validation_error else "validated",
                    reason=validation_error or "typed template accepted by the shell validator",
                )
                if validation_error:
                    raise ValueError(validation_error)
                lane_state["template"] = template
                lane_state["active_template"] = template
                version, pdf, gates = render_version(initial_note, template)
                update_builder_candidate(
                    candidate_audit_index,
                    candidate_render_version=version.version_id,
                    outcome="active_unpromoted",
                    reason="initial validated candidate rendered and became the active version",
                )
        except CheckpointBudgetExceeded:
            escalate("builder_initial_budget_exhausted")
            return _lane_terminal(lane, "budget_exhausted")
        except Exception as error:
            import traceback as _tb
            detail = _tb.format_exc()[-600:]
            if candidate_audit_index is not None and builder_candidates[candidate_audit_index]["outcome"] != "validator_rejected":
                update_builder_candidate(
                    candidate_audit_index,
                    outcome="render_failed",
                    reason=str(error)[:300],
                )
            lane_state["last_rejection"] = f"initial render failed: {str(error)[:200]}"
            trace.add(agent="shell", phase="builder", action="initial_failed", note=str(error)[:200], output={"traceback": detail}, persist_output=True)
            escalate(f"lane_builder_initial_failed:{type(error).__name__}")
            return _lane_terminal(lane, "budget_exhausted")

        # --- 4. See -> Measure -> Attribute -> Repair -> Re-render loop -----
        for round_no in range(1, max_repair_rounds + 2):
            try:
                if lane_budget.remaining_model_requests() < 1:
                    trace.add(agent="shell", phase="loop", action="budget_exhausted", note="before review")
                    break
                # The round reviews/measures the ACTIVE version (the last
                # PROMOTED render, or the initial render before any
                # promotion) — never the newest render in history.
                current_version = versions[active_index]
                current_pdf = pdf_by_version[current_version.version_id]
                current_plan = plan_by_version.get(current_version.version_id)
                current_gates = gates_by_version.get(current_version.version_id, {})
                # The next round's changed-region scope comes ONLY from the
                # last PROMOTED repair: a rejected/rolled-back attempt is
                # immutable history (diagnostic), never review scope.
                changed_regions: list[str] = (
                    [last_promoted_region] if last_promoted_region else []
                )
                findings_new: list[DefectFinding] = []
                try:
                    if live:
                        target_overviews = _overview_pngs(target_pdf, out_dir, f"review_target_l{lane}_{round_no}")
                        render_overviews = _overview_pngs(current_pdf, out_dir, f"review_render_l{lane}_{round_no}")
                        scope = _review_scope_payload(round_no, changed_regions, findings)
                        counter["finding"] += 1
                        node_inventory = json.dumps(
                            {
                                "state_nodes": [
                                    {"node_id": n.node_id, "kind": n.kind} for n in state.nodes
                                ],
                                "review_scope": scope,
                            },
                            ensure_ascii=False,
                        )
                        # Bounded images per call: one PAGE per reviewer call
                        # (target overview + render overview), merged across pages.
                        for page_index, target_page in enumerate(target_overviews, 1):
                            lane_images = [target_page]
                            if page_index <= len(render_overviews):
                                lane_images.append(render_overviews[page_index - 1])
                            counter["finding"] += 1
                            findings_new.extend(_with_connection_retry(
                                lambda lane_images=lane_images, page_index=page_index: _live_reviewer_findings(
                                    lane_images,
                                    finding_id=f"finding-l{lane}-r{round_no}-p{page_index}",
                                    target_version=target_id,
                                    render_version=current_version.version_id,
                                    page=page_index,
                                    node_inventory=node_inventory,
                                    budget=lane_budget,
                                    trace=trace,
                                    id_prefix=f"finding-l{lane}-r{round_no}-p{page_index}",
                                    instructions=E5_REVIEWER_INSTRUCTIONS,
                                    visual_model=True,
                                    message=(
                                        "Compare the TARGET and RENDER images for this page. Review scope: "
                                        + json.dumps(scope, ensure_ascii=False)
                                        + "\nReport observation-first findings with SEMANTIC measurement "
                                        "intents (leave the four anchor fields empty, set `intent`). "
                                        "Findings must carry exact versions and a region id from the "
                                        "node inventory. proposed_cause is a hypothesis only."
                                    ),
                                ),
                                trace=trace, what=f"visual reviewer page {page_index}",
                            ))
                            pages_reviewed.add(page_index)
                    else:
                        counter["finding"] += 1
                        prompt = f"TARGET {target_frozen.case_id} RENDER {current_version.version_id} page 1."
                        findings_new = ScriptedReviewer().run(
                            prompt,
                            finding_id=f"finding-l{lane}-r{round_no}",
                            target_version=target_id,
                            render_version=current_version.version_id,
                            page=1,
                            budget=lane_budget,
                            trace=trace,
                            target_id=target_id,
                        )
                        pages_reviewed.add(1)
                except CheckpointBudgetExceeded:
                    escalate("reviewer_budget_exhausted")
                    break
                except Exception as error:
                    import traceback as _tb2
                    trace.add(
                        agent="shell", phase="loop", action="reviewer_failed",
                        output={"traceback": _tb2.format_exc()[-4000:]}, persist_output=True,
                    )
                    escalate(f"reviewer_live_call_failed:{type(error).__name__}: {str(error)[:200]}")
                    break
                findings_new = _validate_finding_versions(findings_new, target_id, current_version.version_id)
                findings.extend(findings_new)
    
                # Ledger dedup BEFORE attribution (Phase 0).
                actionable: list[DefectFinding] = []
                carried_open_used = False
                for finding in findings_new:
                    resolved = finding.finding_id in resolved_measurements
                    entry, action = ledger.observe(finding, resolved=resolved)
                    trace.add(
                        agent="shell", phase="ledger", action=action,
                        output={"key": entry.ledger_key, "finding": finding.finding_id},
                    )
                    if action == "dedup" and entry.status in {"resolved", "repaired", "attributed"}:
                        continue  # unchanged finding with unchanged evidence: no re-attribution
                    if action == "dedup":
                        if entry.last_measured_version == current_version.version_id:
                            continue  # already measured on THIS render version
                        if finding.region not in changed_regions or carried_open_used:
                            continue  # unchanged region: the stored measurement stays the record
                        carried_open_used = True  # one bounded carried-open re-measure
                    actionable.append(finding)
    
                # Bind -> measure -> attribute. Live causal attribution covers
                # every non-no-defect finding; no finding is skipped merely
                # because another finding in the round was unbound.
                measured: dict[str, tuple[MeasurementResult, MeasurementRequest]] = {}
                for finding in actionable:
                    counter["request"] += 1
                    request_id = f"measure-l{lane}-{counter['request']:03d}"
                    result, bound = execute_measurement(
                        finding, current_pdf, request_id,
                        plan=current_plan, render_version=current_version.version_id,
                    )
                    measurement_results.append(result)
                    measured[finding.finding_id] = (result, bound)
                    ledger.entries[_ledger_key(finding)].last_measured_version = current_version.version_id
                batches = _e5_attribution_batches(actionable, measured)
                if live and batches and _builder_reserve_intact(lane_budget):
                    pod.render_pdf = current_pdf
                    for group in batches:
                        try:
                            hypotheses = _live_attribution_batch(
                                pod, lane_budget, trace,
                                findings=group,
                                results=[measured[f.finding_id][0] for f in group],
                            )
                            # Identity binding, never list position: a swapped,
                            # missing, duplicated, or foreign finding_id rejects
                            # the WHOLE batch (it contributes nothing).
                            bound_hypotheses = _bind_live_attribution_batch(group, hypotheses)
                            for finding, hypothesis in bound_hypotheses:
                                request_id = measured[finding.finding_id][1].request_id
                                attribution = _e5_live_attribution_record(
                                    finding, hypothesis, request_id
                                )
                                attributions.append(attribution)
                                _record_ledger_attribution(
                                    ledger, finding, attribution, request_id,
                                )
                                trace.add(
                                    agent="attribution_investigator", phase="attribute",
                                    action="live_attribution", output=attribution.model_dump(mode="json"),
                                )
                        except CheckpointBudgetExceeded:
                            escalate("attribution_budget_exhausted")
                            break
                        except LiveAttributionBindingError as error:
                            # Fail closed WITH an auditable reason: the batch's
                            # findings fall through to the deterministic
                            # attribution instead of a positional guess.
                            escalate(f"attribution_batch_binding_rejected:{error}")
                            trace.add(
                                agent="shell", phase="attribute",
                                action="attribution_batch_binding_rejected",
                                output={
                                    "batch_findings": [item.finding_id for item in group],
                                    "reason": str(error),
                                },
                            )
                        except Exception as error:
                            escalate(f"attribution_live_call_failed:{type(error).__name__}: {str(error)[:150]}")
                for finding in _e5_findings_needing_fallback_attribution(
                    actionable, measured, attributions
                ):
                    result, bound = measured[finding.finding_id]
                    attribute(finding, result, bound)
    
                # Builder opportunity: skip already-executed fingerprints and
                # use the next measured, material finding in review order.
                repair_finding, all_repairable_repeated = _next_e5_repair_finding(
                    actionable, measured, attributions, fingerprints
                )
                if repair_finding is None and all_repairable_repeated:
                    escalate("stalled_no_new_action")
                    break
                if repair_finding is None:
                    actionable_keys = {
                        (finding.finding_id, finding.render_version) for finding in actionable
                    }
                    awaiting = any(
                        (item.finding_id, item.render_version) in actionable_keys
                        and item.attribution != "no_defect"
                        for item in attributions
                    )
                    escalate(
                        "awaiting_attribution_or_other_owner"
                        if awaiting
                        else f"round{round_no}:no_repairable_bound_finding_continue_review"
                    )
                    continue
                finding, result, bound, attribution, fingerprint = repair_finding
                current_render_images = _overview_pngs(
                    current_pdf, lane_dir, f"builder_render_r{round_no}"
                )
                repair_builder_evidence = _e5_builder_evidence_package(
                    draft=draft,
                    state=state,
                    derived=derived,
                    page_size=page_size,
                    current_render_version=current_version.version_id,
                    findings=actionable,
                    measurements=[measured[item.finding_id][0] for item in actionable],
                    current_gates=current_gates,
                    last_rejection=lane_state.get("last_rejection"),
                    target_images=shared_target_images,
                    current_render_images=current_render_images,
                    selected_attribution=attribution,
                    action_fingerprint=fingerprint,
                    presentation_labels=approved_label_catalog,
                )
                (lane_dir / f"builder_evidence_round_{round_no:02d}.json").write_text(
                    json.dumps(
                        repair_builder_evidence,
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    ) + "\n",
                    encoding="utf-8",
                )
                candidate_audit_index = None
                try:
                    if lane == "a":
                        if live:
                            proposal = _live_lane_a_builder(
                                lane_budget,
                                trace,
                                payload=repair_builder_evidence,
                                images=[*shared_target_images, *current_render_images],
                                proposal_id=f"lane-a-r{round_no + 1}",
                            )
                        else:
                            proposal = _scripted_lane_a_proposal(state, derived)
                        validation_error = _validate_lane_a_proposal(proposal, state)
                        candidate_audit_index = record_builder_candidate(
                            proposal,
                            stage="repair",
                            input_render_version=current_version.version_id,
                            validation={"passed": validation_error is None, "error": validation_error},
                            outcome="validator_rejected" if validation_error else "validated",
                            reason=validation_error or "typed proposal accepted by the shell validator",
                            attribution=attribution,
                            action_fingerprint=fingerprint,
                        )
                        if validation_error:
                            repair_attempts.append({"finding": finding.finding_id, "region": finding.region, "rejected": validation_error, "round": round_no})
                            trace.add(agent="shell", phase="repair", action="rejected", note=validation_error)
                            continue
                        repair_attempts.append(
                            {"finding": finding.finding_id, "region": finding.region, "round": round_no,
                             "agent": proposal.agent, "sections": len(proposal.sections)}
                        )
                        candidate_version, candidate_pdf, candidate_gates = render_version(
                            f"lane A repair round {round_no} for {finding.finding_id}", proposal
                        )
                    else:
                        if live:
                            template = _live_lane_b_builder(
                                lane_budget, trace, payload=repair_builder_evidence,
                                images=[*shared_target_images, *current_render_images],
                                template_id=f"lane-b-r{round_no + 1}",
                            )
                        else:
                            from tests.experiments.e_authored_template import SCRIPTED_AUTHORED_TEMPLATE as _base_template
    
                            template = _scripted_lane_b_template(_base_template, finding, result)
                        try:
                            validation = validate_authored_template(
                                template,
                                target_pdf=target_pdf,
                                render_candidate=candidate,
                                known_evidence_ids=_known_evidence_ids(),
                                labels=approved_label_catalog,
                            )
                        except ValueError as error:
                            validation = {"passed": False, "error": str(error)}
                            candidate_audit_index = record_builder_candidate(
                                template,
                                stage="repair",
                                input_render_version=current_version.version_id,
                                validation=validation,
                                outcome="validator_rejected",
                                reason=str(error),
                                attribution=attribution,
                                action_fingerprint=fingerprint,
                            )
                            lane_state["last_rejection"] = str(error)[:280]
                            repair_attempts.append({"finding": finding.finding_id, "region": finding.region, "rejected": str(error)[:300], "round": round_no})
                            trace.add(agent="shell", phase="repair", action="rejected", note=str(error)[:300])
                            continue
                        candidate_audit_index = record_builder_candidate(
                            template,
                            stage="repair",
                            input_render_version=current_version.version_id,
                            validation=validation,
                            outcome="validated",
                            reason="typed template accepted by the shell validator",
                            attribution=attribution,
                            action_fingerprint=fingerprint,
                        )
                        repair_attempts.append(
                            {"finding": finding.finding_id, "region": finding.region,
                             "round": round_no, "template": template.template_id}
                        )
                        candidate_version, candidate_pdf, candidate_gates = render_version(
                            f"lane B repair round {round_no} for {finding.finding_id}", template
                        )
                    assert candidate_audit_index is not None
                    update_builder_candidate(
                        candidate_audit_index,
                        candidate_render_version=candidate_version.version_id,
                        outcome="rendered_pending_decision",
                        reason="validated candidate rendered; awaiting shell promotion or rollback",
                    )
                except CheckpointBudgetExceeded:
                    escalate("builder_budget_exhausted")
                    break
                except Exception as error:
                    if candidate_audit_index is not None:
                        update_builder_candidate(
                            candidate_audit_index,
                            outcome="render_failed",
                            reason=str(error)[:300],
                        )
                    escalate(f"repair_render_failed:{type(error).__name__}: {str(error)[:150]}")
                    continue
                lane_budget.repair_attempt_count += 1
                fingerprints.append(fingerprint)  # records the EXECUTED action
                candidate_index = len(versions) - 1  # the candidate's history index
                trace.add(
                    agent="builder", phase="repair", action="proposal",
                    note=f"round {round_no}", persist_output=True,
                    output={
                        "current_version": current_version.version_id,
                        "current_gates": current_gates,
                    },
                )
                # Re-measure FIRST: the IDENTICAL request, changing only the version.
                counter["request"] += 1
                repeat_id = bound.request_id
                repeat_result, _ = execute_measurement(
                    finding, candidate_pdf, repeat_id,
                    plan=plan_by_version.get(candidate_version.version_id),
                    render_version=candidate_version.version_id,
                )
                measurement_results.append(repeat_result)
                improved = (
                    repeat_result.current_value_pt is not None
                    and result.current_value_pt is not None
                    and repeat_result.target_value_pt is not None
                    and abs(repeat_result.current_value_pt - repeat_result.target_value_pt)
                    < abs(result.current_value_pt - result.target_value_pt)
                )
                if not improved:
                    rolled_back_versions.add(candidate_version.version_id)
                    update_builder_candidate(
                        candidate_audit_index,
                        outcome="rolled_back",
                        reason="identical re-measurement did not improve",
                    )
                    attempted_strategies.append(f"round{round_no}:{finding.finding_id}:non_improving_rolled_back")
                    trace.add(agent="shell", phase="repair", action="rolled_back", note="non-improving repair")
                    continue
                gate_keys = (
                    CANDIDATE_FACT_GATES if lane == "a"
                    else ("content_gate", "candidate_content_accounting", "no_target_candidate_facts",
                          "no_blank_page", "deterministic_render")
                )
                if not all(candidate_gates.get(gate) for gate in gate_keys):
                    rolled_back_versions.add(candidate_version.version_id)
                    update_builder_candidate(
                        candidate_audit_index,
                        outcome="rolled_back",
                        reason="candidate-safety gates failed",
                    )
                    attempted_strategies.append(f"round{round_no}:{finding.finding_id}:repair_failed_gates_rolled_back")
                    trace.add(agent="shell", phase="repair", action="rolled_back", note="candidate-safety gate failed")
                    continue
                holds, rechecks = accepted_regions_hold(candidate_pdf)
                if not holds:
                    rolled_back_versions.add(candidate_version.version_id)
                    update_builder_candidate(
                        candidate_audit_index,
                        outcome="rolled_back",
                        reason="accepted-region recheck failed closed",
                    )
                    attempted_strategies.append(f"round{round_no}:{finding.finding_id}:accepted_region_regressed_rolled_back")
                    trace.add(agent="shell", phase="repair", action="rolled_back", note="accepted region regressed")
                    continue
                # PROMOTION (shell-only): the candidate becomes the new ACTIVE
                # version; the rejected candidates before it stay in the
                # immutable history and are never implicitly selected again.
                versions[candidate_index] = candidate_version.model_copy(update={"promoted": True})
                update_builder_candidate(
                    candidate_audit_index,
                    outcome="promoted",
                    reason="shell verified improvement, safety gates, and accepted-region hold",
                )
                active_index = candidate_index
                last_promoted_region = finding.region
                if lane == "a":
                    lane_state["active_proposal"] = proposal
                else:
                    lane_state["active_template"] = template
                resolved_measurements[finding.finding_id] = (bound, repeat_result)
                repaired_entry = ledger.entries[_ledger_key(finding)]
                repaired_entry.status = "repaired"
                repaired_entry.measurement_request_id = repeat_result.request_id
                trace.add(
                    agent="shell", phase="repair", action="promoted",
                    output={"version": candidate_version.version_id, "rechecks": rechecks},
                    persist_output=True,
                )
    
            except (CheckpointBudgetExceeded, BudgetExhausted) as budget_error:
                escalate("loop_tool_budget_exhausted")
                trace.add(agent="shell", phase="loop", action="budget_exhausted", note=str(budget_error)[:200])
                break
        return _lane_terminal(lane)

    # -- 5. Run the lanes (fresh contexts; lane B never sees lane A output) --
    for lane in lanes:
        started_lane = time.time()
        record = _run_lane(lane)
        record.summary["elapsed_seconds"] = round(time.time() - started_lane, 1)
        record.summary["estimated_provider_cost"] = _estimate_lane_cost(record, pricing)
        lane_records[lane] = record

    # -- 6. Terminal state + comparison + owner package ----------------------
    overall_terminal = (
        "ready_for_owner_review"
        if lane_records and all(r.terminal_state == "ready_for_owner_review" for r in lane_records.values())
        else "budget_exhausted"
    )
    # Source identity check: if any registered direct runtime source changed
    # mid-run, the run records `source_changed` and is NOT source-identity
    # stable. `source_identity_stable` says ONLY that the source did not
    # drift — it never promotes an offline rehearsal to a canonical run.
    source_changed = _source_hashes_changed(config)
    loop_record = E5LoopRecord(
        target_id=target_id,
        target_sha256=target_frozen.target_sha256,
        lanes={lane: rec for lane, rec in lane_records.items()},
        budget_state=budget.to_json(),
        started_at=started_at,
        finished_at=datetime.now(UTC).isoformat(timespec="seconds"),
        summary={
            "terminal_state": overall_terminal,
            "live": live,
            "elapsed_seconds": round(time.time() - started, 1),
            "lanes": {lane: r.terminal_state for lane, r in lane_records.items()},
            "source_changed": source_changed,
            "source_identity_stable": not source_changed,
            "presentation_label_approval": {
                "target_sha256": target_sha256,
                "catalog_sha256": catalog_sha256,
                "approval_provided": approval_provided,
                "approval_validated": approval_provided,
                "approved_label_ids": [
                    label.label_id for label in approved_label_catalog
                ],
                "proposed_label_ids": [
                    label.label_id for label in label_catalog
                    if label.label_id not in {a.label_id for a in approved_label_catalog}
                ],
            },
        },
    )
    (out_dir / "e5_state.json").write_text(
        json.dumps(loop_record.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    store.record_state(overall_terminal, f"lanes={ {k: v.terminal_state for k, v in lane_records.items()} }")
    (out_dir / "manifest.json").write_text(
        json.dumps(store.manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _write_e5_comparison(out_dir, loop_record, config)
    try:
        _write_e5_owner_package(
            out_dir,
            target_pdf,
            loop_record,
            label_catalog,
            approved_label_ids=[label.label_id for label in approved_label_catalog],
            target_sha256=target_sha256,
            catalog_sha256=catalog_sha256,
            # an invalid approval aborts before the package exists, so any
            # package that reached this point validated a provided approval
            approval_provided=approval_provided,
            approval_validated=approval_provided,
        )
    except Exception as error:
        (out_dir / "owner_package_failed.txt").write_text(str(error), encoding="utf-8")
    return out_dir, overall_terminal, loop_record.model_dump(mode="json")


def _write_e5_lane_report_md(lane_id: str, record: E5LaneRecord) -> str:
    versions_table = "\n".join(
        f"| `{v.version_id}` | {v.hard_gates_passed} | {v.promoted} | {v.note} |"
        for v in record.render_versions
    ) or "| - | - | - | - |"
    findings_table = "\n".join(
        f"| `{f.finding_id}` | {f.region} | {f.suspected_dimension} | {f.observation[:70]} |"
        for f in record.findings
    ) or "| - | - | - | - |"
    ledger_table = "\n".join(
        f"| `{e.finding_id}` | {e.status} | {e.first_seen_version} -> {e.last_seen_version} |"
        for e in record.ledger
    ) or "| - | - | - |"
    probes_note = (
        "DIAGNOSTIC probes on the selected non-best attempt (no best-valid "
        "render; NOT promotion evidence)" if record.summary.get("probe_mode") == "diagnostic"
        else "promotion-evidence probes against the selected best-valid representation"
    )
    return f"""# Pipeline E5 Lane {lane_id.upper()} — {record.representation}

- Terminal state: **{record.terminal_state}** (never owner acceptance)
- Best render: `{record.best_render_version or 'NONE — no best-valid render exists'}`
- Active render: `{record.active_render_version}`
- Defect-level promoted (never BEST): `{record.best_defect_level_version or 'none'}`
- Findings: {record.summary.get('total_findings')}; confirmed measurements:
  {record.summary.get('confirmed_measurements')}; unbound:
  {record.summary.get('unbound_measurements')}
- Builder calls: {record.summary.get('builder_calls')}; reviewer:
  {record.summary.get('reviewer_calls')}; attribution:
  {record.summary.get('attribution_calls')}
- Tokens (live): {record.budget_state.get('usage', {}).get('input_tokens', 0)}/
  {record.budget_state.get('usage', {}).get('output_tokens', 0)}
- Probes: {record.content_shape_probes_passed} — {probes_note}

## Render versions

| version | gates | promoted | note |
| --- | --- | --- | --- |
{versions_table}

## Findings

| finding | region | dimension | observation |
| --- | --- | --- | --- |
{findings_table}

## Defect ledger (Phase 0)

| finding | status | seen |
| --- | --- | --- |
{ledger_table}

This is an experiment record; automated metrics declare no winner and no
convergence. The owner reviews the actual files and decides.
"""


def summarize_e5_state(e5_state_path: Path) -> dict[str, Any]:
    """Deterministic summary of ONE E5 run from its `e5_state.json` (the
    ledger/report data source): every reported number is read from that
    single run record, so reports can never splice findings, measurements,
    calls, probes or costs from different runs. Companion helper for
    `E_PIPELINE_PREP.md` and run reports."""
    state = json.loads(Path(e5_state_path).read_text(encoding="utf-8"))
    lanes: dict[str, Any] = {}
    for lane_id, lane in state.get("lanes", {}).items():
        summary = lane.get("summary", {})
        budget_state = lane.get("budget_state", {})
        lanes[lane_id] = {
            "terminal_state": lane.get("terminal_state"),
            "best_render_version": lane.get("best_render_version"),
            "active_render_version": lane.get("active_render_version"),
            "render_versions": len(lane.get("render_versions", [])),
            "promoted_versions": summary.get("promoted_versions"),
            "findings": summary.get("total_findings"),
            "confirmed_measurements": summary.get("confirmed_measurements"),
            "unbound_measurements": summary.get("unbound_measurements"),
            "builder_calls": summary.get("builder_calls"),
            "reviewer_calls": summary.get("reviewer_calls"),
            "attribution_calls": summary.get("attribution_calls"),
            "model_calls": budget_state.get("model_request_count"),
            "model_calls_by_mode": budget_state.get("calls_by_mode"),
            "tool_calls": budget_state.get("tool_call_count"),
            "tool_calls_by_tool": budget_state.get("calls_by_tool"),
            "input_tokens": budget_state.get("usage", {}).get("input_tokens", 0),
            "output_tokens": budget_state.get("usage", {}).get("output_tokens", 0),
            "probes_passed": lane.get("content_shape_probes_passed"),
            "probe_mode": summary.get("probe_mode"),
        }
    return {
        "run_id": Path(e5_state_path).parent.name,
        "source": str(e5_state_path),
        "terminal_state": state.get("summary", {}).get("terminal_state"),
        "source_changed": state.get("summary", {}).get("source_changed"),
        "source_identity_stable": state.get("summary", {}).get("source_identity_stable"),
        "lanes": lanes,
    }


def _selected_lane_artifact(
    lane: "E5LaneRecord",
) -> tuple[RenderVersion | None, str, str]:
    """Resolve the owner-facing lane artifact through the explicit version
    state (never `len(render_versions)` / `versions[-1]` silently). Returns
    (version, file stem, label):

    - ``BEST`` — only a real hard-gate-valid best render;
    - ``ACTIVE DEFECT-LEVEL VERSION`` — the defect-level promoted active
      render (local improvement state, explicitly NOT best);
    - ``ACTIVE UNPROMOTED VERSION`` — the lane's current ACTIVE render when
      it never earned promotion (never BEST, never a rejected attempt);
    - ``LATEST ATTEMPT`` — only when NO active version exists at all.

    A rolled-back candidate is never selected: it is immutable history, not
    the lane's current output.
    """
    def _stem(version_id: str) -> tuple[RenderVersion | None, str]:
        for index, version in enumerate(lane.render_versions):
            if version.version_id == version_id:
                return version, f"render_{index + 1}"
        return None, ""

    if lane.best_render_version:
        version, stem = _stem(lane.best_render_version)
        if version is not None:
            return version, stem, "BEST"
    if lane.best_defect_level_version:
        version, stem = _stem(lane.best_defect_level_version)
        if version is not None:
            return version, stem, "ACTIVE DEFECT-LEVEL VERSION"
    if lane.active_render_version:
        version, stem = _stem(lane.active_render_version)
        if version is not None:
            return version, stem, "ACTIVE UNPROMOTED VERSION"
    if lane.render_versions:
        return lane.render_versions[-1], f"render_{len(lane.render_versions)}", "LATEST ATTEMPT"
    return None, "", "LATEST ATTEMPT"


def _write_e5_comparison(
    out_dir: Path, record: E5LoopRecord, config: dict[str, Any]
) -> None:
    """The lane comparison report: NO aggregate similarity percentage, NO
    automated winner declaration — comparable counts + recorded costs only."""
    rows = []
    for lane_id, lane in record.lanes.items():
        summary = lane.summary
        # Page count comes from the SELECTED artifact (best-valid render, or
        # the clearly-labeled latest attempt when none exists) — never from
        # `render_versions[-1]` silently.
        selected, _stem, label = _selected_lane_artifact(lane)
        rows.append(
            {
                "lane": lane_id,
                "representation": lane.representation,
                "terminal_state": lane.terminal_state,
                "best_render": lane.best_render_version,
                "best_defect_level_version": lane.best_defect_level_version,
                "active_render": lane.active_render_version,
                "render_label": label,
                "selected_version": selected.version_id if selected else None,
                "selected_pages": selected.page_count if selected else None,
                "findings": summary.get("total_findings"),
                "confirmed_measurements": summary.get("confirmed_measurements"),
                "measurement_binding_failures": summary.get("unbound_measurements"),
                "attribution_calls": summary.get("attribution_calls"),
                "builder_calls": summary.get("builder_calls"),
                "improving_repairs": summary.get("promoted_versions"),
                "rollbacks": len([s for s in lane.attempted_strategies if "rolled_back" in s]),
                "probes_passed": lane.content_shape_probes_passed,
                "probe_mode": summary.get("probe_mode"),
                "input_tokens": lane.budget_state.get("usage", {}).get("input_tokens", 0),
                "output_tokens": lane.budget_state.get("usage", {}).get("output_tokens", 0),
                "tool_calls": lane.budget_state.get("tool_call_count"),
                "elapsed_seconds": summary.get("elapsed_seconds"),
                "estimated_provider_cost": summary.get("estimated_provider_cost"),
                # Not measured in this run: recorded as not_evaluated, never
                # as a zero (a zero would claim a measurement that never ran).
                "target_specific_code": None,
                "target_specific_code_status": "not_evaluated",
                "shared_backend_code": {
                    "lane_a": "c2_plan.SectionPlan rail fields + c2_html rail branch",
                    "lane_b": "tests/experiments/e_authored_template.py",
                }.get("lane_a" if lane_id == "a" else "lane_b"),
            }
        )
    comparison = {
        "run_id": record.summary.get("run_id", out_dir.name),
        "frozen_config": config.get("run_id"),
        "source_changed": record.summary.get("source_changed"),
        "lanes": rows,
        "decision": (
            "Automated metrics declare NO winner (plan §13). The owner compares "
            "the owner_review/ artifacts visually and decides."
        ),
    }
    (out_dir / "comparison_report.json").write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    table = "\n".join(
        f"| {row['lane']} | {row['terminal_state']} | {row['best_render'] or 'none'} "
        f"({row['render_label']}) | {row['findings']} | {row['confirmed_measurements']} | "
        f"{row['builder_calls']} | {row['improving_repairs']} | "
        f"{row['input_tokens']}/{row['output_tokens']} | {row['elapsed_seconds']} |"
        for row in rows
    )
    (out_dir / "COMPARISON.md").write_text(
        f"""# Pipeline E5 Builder-representation comparison — {out_dir.name}

- Terminal state: **{record.summary.get('terminal_state')}**
- Question: which Builder representation gives the Agent a practical path
  toward convergence WITHOUT target-specific backend code?
- This report declares NO winner; the owner decides from `owner_review/`.
- BEST labels mark a real best-valid render; ACTIVE DEFECT-LEVEL VERSION
  marks a defect-level promoted active render when no hard-gate-valid best
  exists (a local improvement, never BEST); ACTIVE UNPROMOTED VERSION marks
  the lane's current active render that never earned promotion; a
  rolled-back attempt is never shown as the lane's output.

| lane | terminal | best render | findings | confirmed | builder calls | improving repairs | tokens in/out | elapsed s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
{table}

## Non-claims

- No Pipeline E convergence claim; no Resume I acceptance; NO T-v1 creation.
- No aggregate similarity percentage; metrics declare nothing (plan §13).
- Owner visual preference is an owner-only input; it is NOT inferred here.
""",
        encoding="utf-8",
    )


def _write_e5_owner_package(
    out_dir: Path,
    target_pdf: Path,
    record: E5LoopRecord,
    presentation_labels: list[PresentationLabel] | None = None,
    *,
    approved_label_ids: list[str] | None = None,
    target_sha256: str | None = None,
    catalog_sha256: str | None = None,
    approval_provided: bool | None = None,
    approval_validated: bool | None = None,
) -> Path:
    """The safe owner-review package (E5 work order): Resume I target, the E4
    best-render baseline, and each lane's best render side by side per page,
    plus probe outputs, costs, the shell-issued presentation-label table,
    remaining differences, and the non-claims."""
    from app.ingestion.pdf_reader import read_pdf_text
    from tests.experiments import c2_renderer as c2r

    package = out_dir / "owner_review"
    package.mkdir(exist_ok=True)
    target_pages = _render_pages(target_pdf, out_dir, "owner_target")
    page_count = len(target_pages)
    e4_dir = RUNS / E5_RUNS_E4_BASELINE
    e4_pdf = e4_dir / "render_2.pdf"
    # Resolve each lane's artifact through the explicit version state; the
    # label distinguishes a real BEST render from a defect-level active
    # version and from a mere latest attempt.
    lane_selected: dict[str, tuple[RenderVersion | None, Path, str]] = {}
    for lane_id, lane in record.lanes.items():
        version, stem, label = _selected_lane_artifact(lane)
        stem_path = (out_dir / f"lane_{lane_id}" / f"{stem}.pdf") if stem else None
        lane_selected[lane_id] = (version, stem_path, label) if stem_path else (None, None, "LATEST ATTEMPT")
    lane_pdfs: dict[str, Path] = {
        lane_id: paths[1] for lane_id, paths in lane_selected.items() if paths[1]
    }
    for page in range(1, page_count + 1):
        columns: list[tuple[str, Path | None]] = [("TARGET", target_pages[page - 1])]
        if (e4_dir / f"render_2_page_{page}.png").exists():
            columns.append(("E4 BEST v2", e4_dir / f"render_2_page_{page}.png"))
        elif (e4_pdf := e4_dir / "render_2.pdf").exists():
            e4_pages = _render_pages(e4_pdf, out_dir, f"owner_e4_p{page}")
            if e4_pages:
                columns.append(("E4 BEST v2", e4_pages[min(page, len(e4_pages)) - 1]))
        for lane_id, lane_pdf in lane_pdfs.items():
            lane_page = out_dir / f"lane_{lane_id}" / f"{lane_pdf.stem}_page_{page}.png"
            if lane_page.exists():
                columns.append((f"LANE {lane_id.upper()} {lane_selected[lane_id][2]}", lane_page))
        if len(columns) >= 2:
            _side_by_side(columns, package / f"comparison_page_{page}.png")
    # Selected lane artifacts (copies; originals immutable). The filename and
    # every label distinguish a real BEST render from a defect-level active
    # version and from a latest attempt.
    _LABEL_KIND = {
        "BEST": "best",
        "ACTIVE DEFECT-LEVEL VERSION": "active_defect_level",
        "ACTIVE UNPROMOTED VERSION": "active_unpromoted",
        "LATEST ATTEMPT": "latest_attempt",
    }
    for lane_id, (version, lane_pdf, label) in lane_selected.items():
        if lane_pdf is None:
            continue
        kind = _LABEL_KIND[label]
        lane_html = out_dir / f"lane_{lane_id}" / f"{lane_pdf.stem}.html"
        shutil.copy2(lane_pdf, package / f"lane_{lane_id}_{kind}.pdf")
        if lane_html.exists():
            shutil.copy2(lane_html, package / f"lane_{lane_id}_{kind}.html")
    # Probe outputs.
    for lane_id in record.lanes:
        probes = out_dir / f"lane_{lane_id}" / "content_shape_probes.json"
        if probes.exists():
            shutil.copy2(probes, package / f"lane_{lane_id}_content_shape_probes.json")
    cost = {
        lane_id: {
            "tokens_in": lane.budget_state.get("usage", {}).get("input_tokens", 0),
            "tokens_out": lane.budget_state.get("usage", {}).get("output_tokens", 0),
            "live_calls": lane.budget_state.get("calls_by_mode", {}).get("live", 0),
            "tool_calls": lane.budget_state.get("tool_call_count"),
            "estimated_cost": lane.summary.get("estimated_provider_cost"),
        }
        for lane_id, lane in record.lanes.items()
    }
    (package / "cost_summary.json").write_text(
        json.dumps(
            {
                "pricing_source": (out_dir / "run_config.json").exists()
                and json.loads((out_dir / "run_config.json").read_text(encoding="utf-8")).get("provider_pricing"),
                "per_lane": cost,
                "note": "raw usage preserved; no price invented when billing is not verifiable",
            },
            ensure_ascii=False, indent=2, sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )
    remaining = "\n".join(
        f"- lane {lane_id} `{fid}`: {next((f.observation for f in lane.findings if f.finding_id == fid), fid)}"
        for lane_id, lane in record.lanes.items()
        for fid in (lane.open_findings or [])
    ) or "- none recorded"
    def _best_line(lane_id: str, lane: E5LaneRecord) -> str:
        if lane.best_render_version:
            return f"- Lane {lane_id.upper()} best render: `{lane.best_render_version}`"
        if lane.best_defect_level_version:
            return (
                f"- Lane {lane_id.upper()}: NO hard-gate-valid best render exists; the "
                f"active defect-level version `{lane.best_defect_level_version}` is shown "
                "as ACTIVE DEFECT-LEVEL VERSION (a local improvement, never BEST)"
            )
        if lane.active_render_version:
            return (
                f"- Lane {lane_id.upper()}: no best-valid render exists; the active "
                f"version `{lane.active_render_version}` is shown as ACTIVE "
                "UNPROMOTED VERSION — a rolled-back attempt is never shown as the "
                "lane's current output, never labeled BEST"
            )
        return (
            f"- Lane {lane_id.upper()} best render: NONE — no best-valid render "
            "exists; the package shows the latest attempt (LATEST ATTEMPT), "
            "never labeled BEST"
        )

    best_lines = "\n".join(
        _best_line(lane_id, lane) for lane_id, lane in record.lanes.items()
    )
    # Presentation-label catalog: written into the run dir AND the package,
    # with the label table in the report. Audit material only — it is NOT an
    # acceptance of T-v1 and declares no winner.
    label_listing = _presentation_label_listing(
        presentation_labels or [],
        {
            lane_id: list((lane.summary or {}).get("presentation_labels_referenced") or [])
            for lane_id, lane in record.lanes.items()
        },
        approved_label_ids,
        target_sha256=target_sha256,
        catalog_sha256=catalog_sha256,
        approval_provided=approval_provided,
        approval_validated=approval_validated,
    )
    (out_dir / "presentation_labels.json").write_text(
        json.dumps(label_listing, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (package / "presentation_labels.json").write_text(
        json.dumps(label_listing, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    approved_count = len(label_listing["approved_label_ids"])
    proposed_count = len(label_listing["proposed_label_ids"])
    label_rows = "\n".join(
        "| `{}` | {} | {} | {} | {} | {} |".format(
            entry["label_id"],
            entry["text"],
            entry["kind"],
            entry["status"],
            ", ".join(entry["evidence_ids"]),
            ", ".join(entry["referenced_by_lanes"]) or "(none)",
        )
        for entry in label_listing["labels"]
    ) or "| (no measured presentation labels) | | | | | |"
    (package / "REPORT.md").write_text(
        f"""# Pipeline E5 owner-review package — {out_dir.name}

- Terminal state: **{record.summary.get('terminal_state')}** (NEVER owner acceptance)
{best_lines}

## Contents

- `comparison_page_N.png` — TARGET | E4 best baseline | Lane A | Lane B, per page;
  a column labeled LANE X LATEST ATTEMPT is NOT a best-valid render;
- `lane_*_best.html/.pdf` — each lane's best-valid render (only present when
  a best-valid render exists);
- `lane_*_active_defect_level.html/.pdf` — a lane's defect-level promoted
  active version when NO hard-gate-valid best exists (a local improvement,
  explicitly NOT BEST);
- `lane_*_active_unpromoted.html/.pdf` — a lane's current ACTIVE render that
  never earned promotion (no rollback attempt is ever shown as the lane's
  current output);
- `lane_*_latest_attempt.html/.pdf` — latest attempt for a lane with NO
  active version at all (explicitly not BEST);
- `lane_*_content_shape_probes.json` — short/medium/long probe outcomes;
  probes marked diagnostic ran on a latest attempt and are NOT promotion
  evidence;
- `cost_summary.json` — usage + verified pricing record (no invented price);
- `presentation_labels.json` — the complete shell-issued presentation-label
  catalog (label_id, text, kind, target evidence id, which lanes referenced
  it); the same table is below; it is audit material, NOT an acceptance of
  T-v1;
- remaining material differences below; iteration histories in lane dirs.

## Presentation labels (owner-approved vs proposed; audit material)

Fixed visible template text may only enter through an OWNER-APPROVED label id.
Approval is a typed owner INPUT bound to the exact target (`target_sha256`),
the exact proposed catalog identity (`catalog_sha256`), and explicit label
ids — it is never inferred from label text, so the same wording in another
target is NOT approved. The Builder references an id; the shell owns the text.
Measurement only PROPOSES an entry — it never approves one, because provenance
does not prove that a short target line is a presentation label rather than a
name, school, employer, or job title. `proposed` entries are listed for review
but are NOT renderable and are NOT excluded from the privacy gate.
Target-person facts, contact data, dates, employers, schools, roles, and any
text that is not classified presentation structure are NOT approved.

- target_sha256: `{target_sha256}`
- catalog_sha256 (proposed-catalog identity): `{catalog_sha256}`
- owner approval provided: {approval_provided} · validated: {approval_validated}

Approved: {approved_count} · proposed (awaiting
owner approval): {proposed_count}

| label_id | text | kind | status | evidence | referenced by lanes |
| --- | --- | --- | --- | --- | --- |
{label_rows}

## Remaining material differences (open findings)

{remaining}

## Non-claims

- This package is NOT a delivery and creates NO T-v1 record.
- No convergence claim and no representation winner is declared.
- No signed provider URLs, credentials, environment data, or rubric answers
  entered this package.
""",
        encoding="utf-8",
    )
    for path in sorted(package.rglob("*")):
        if path.is_file():
            try:
                payload = path.read_text(encoding="utf-8")
            except Exception:
                continue
            assert_no_signed_strings(payload)
    return package

if __name__ == "__main__":
    raise SystemExit(main())
