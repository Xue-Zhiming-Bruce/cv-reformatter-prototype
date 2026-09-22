"""Pipeline E — shared contracts, evidence layer, and runtime plumbing.

Behaviour-neutral module split of `tests/experiments/e_pipeline.py`
(2026-09-22). This module is the LEAF of the Pipeline E import graph: it
imports no other Pipeline E module. It holds only what two or more of the
remaining modules need:

- the versioned typed contracts (`EvidenceModel` family, the E2
  measurement/finding/attribution/render-version records shared by E3-E5);
- the Lane B `PresentationLabel` contract (imported by
  `e_authored_template.py` directly — never through the compat facade);
- the read-only `EvidencePod` / `DualSourcePod` evidence layer, the bounded
  local-measurement helpers, and the E0 frozen-input set;
- the shared live-model plumbing (`_call_limits`, `_live_model_settings`,
  `_live_visual_model`, `_model_identity`) and the read-only evidence tools
  (`_pod_tools`);
- the agent-call message audit (`E5AgentAuditSpec` + JSONL writer) used by the
  E2 live reviewer and the E5 live builders;
- the single live-hypothesis -> `AttributionRecord` conversion shared by the
  E4 single-finding path and the E5 batched path.

Nothing here decides product behaviour; every constant, prompt text, schema
and algorithm was moved verbatim. Terminal states, budgets, gates, privacy
rules and artifact layouts are unchanged.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, NamedTuple
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, model_validator
from tests.experiments.a_pipeline import ROOT, _render_pages
from tests.experiments.d_pipeline import RunBudget, RunTrace

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


class RenderVersion(EvidenceModel):
    """One versioned render with its gate results and promotion state."""

    version_id: str
    html_sha256: str
    pdf_sha256: str
    page_count: int = Field(ge=1)
    hard_gates_passed: bool
    promoted: bool = False
    note: str = ""


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
E4_TEMPERATURE = 0.0  # frozen sampling control (recorded in run_config.json)
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


# --- E5 agent-call message audit (owner decision 2026-09-22: keep the
# model-visible request and provider-returned message history) ---------------


class E5AgentAuditSpec(NamedTuple):
    """Per-call audit identity for ONE live agent invocation. Written to the
    lane's ignored `agent_messages.jsonl` together with the serialized
    model-visible message history; never copied into the owner package."""

    agent: str
    lane: str | None
    phase: str
    round_no: int | None
    target_version: str
    render_version: str | None
    finding_ids: tuple[str, ...]
    measurement_ids: tuple[str, ...]
    model: str | None
    instructions: str
    image_refs: tuple[dict[str, Any], ...]


def _e5_serialize_agent_messages(
    messages: list[Any], image_refs: tuple[dict[str, Any], ...]
) -> str:
    """Serialize the FULL model-visible request/provider-response sequence
    with the installed PydanticAI ``ModelMessagesTypeAdapter`` (the complete
    conversation, including tool calls/results and validation-retry prompts).

    Two SEPARATE steps, in this order:

    1. the LEGITIMATE typed messages are serialized by the official adapter —
       never by a hand-built substitute object, so the recorded role/part
       types, tool identity, retry prompts and ordering stay exactly as
       PydanticAI produced them and no serialization warning is emitted;
    2. only in the ALREADY SERIALIZED plain data tree is every binary payload
       (``kind == "binary"``) replaced by an auditable reference: the call
       site's run-relative path/page/role when available, the media type, and
       the SHA-256 of the ACTUAL bytes (base64-decoded to hash, then
       discarded). Images are NEVER kept as base64 or any large binary.

    URL/file-id content is text and is deliberately left in place: it is
    covered by the caller's signed-URL/secret check. Model internals are not
    recorded. No credential/authorization/environment value can appear here
    because the messages contain model content only; the caller still runs the
    signed-URL/secret check over the final text and fails closed."""
    import base64

    from pydantic_ai.messages import ModelMessagesTypeAdapter

    # Step 1: official adapter over the ORIGINAL typed objects.
    serialized = json.loads(ModelMessagesTypeAdapter.dump_json(messages))

    # Step 2: replace binary payloads in the plain data tree, in the same
    # order the call site listed its image references.
    refs = list(image_refs)
    used = 0

    def _redact(node: Any) -> None:
        nonlocal used
        if isinstance(node, dict):
            if node.get("kind") == "binary" and isinstance(node.get("data"), str):
                ref = (
                    dict(refs[used])
                    if used < len(refs)
                    else {
                        "redacted": True,
                        "note": "image reference not provided by the call site",
                    }
                )
                used += 1
                media_type = node.get("media_type")
                encoded = node["data"]
                encoded += "=" * (-len(encoded) % 4)
                digest = hashlib.sha256(base64.urlsafe_b64decode(encoded)).hexdigest()
                node.clear()
                node.update(
                    {
                        **ref,
                        "kind": "image_reference",
                        "media_type": media_type,
                        "sha256": digest,
                    }
                )
                return
            for value in node.values():
                _redact(value)
        elif isinstance(node, list):
            for item in node:
                _redact(item)

    _redact(serialized)
    return json.dumps(serialized, ensure_ascii=False)


def _e5_agent_audit_record(
    trace: RunTrace,
    spec: E5AgentAuditSpec,
    *,
    input_messages: list[Any],
    run_result: Any | None,
    error: str | None,
    model_name: str | None = None,
) -> str:
    """ONE JSONL line per Agent call in the lane's ignored
    `agent_messages.jsonl` (owner decision 2026-09-22): stable call_id, full
    call identity, model-visible user messages, provider-returned messages
    (including validation-retry prompts and tool calls/results), usage, and
    success/error status. Images are saved as path/hash references, never
    base64. Credentials, env values, and signed URLs must not enter the
    artifact: the signed-URL/secret check runs over the final text and, if it
    fails, the message CONTENT is withheld behind an explicit
    `secret_check_failed` record (never faked, never silently dropped).
    The call_id is the persisted trace artifact name, so trace and audit
    record are joinable."""
    status = "success" if run_result is not None and error is None else "error"
    if error is not None and "usagelimitexceeded" in error.casefold():
        status = "budget_exhausted"
    sequence = len(trace.entries) + 1
    artifact_name = f"trace_{sequence:04d}_agent_call.json"
    # call_id = lane prefix + the persisted trace artifact name: stable,
    # unique across lanes, and directly joinable with the lane trace entry.
    call_id = f"{spec.lane or 'shared'}-{artifact_name}"
    usage = None
    if run_result is not None:
        try:
            usage = run_result.usage
            if callable(usage):
                usage = usage()
            usage = {
                "input_tokens": getattr(usage, "input_tokens", None),
                "output_tokens": getattr(usage, "output_tokens", None),
                "requests": getattr(usage, "requests", None),
                "tool_calls": getattr(usage, "tool_calls", None),
            }
        except Exception:
            usage = None
    meta = {
        "call_id": call_id,
        "agent": spec.agent,
        "lane": spec.lane,
        "phase": spec.phase,
        "round": spec.round_no,
        "target_version": spec.target_version,
        "render_version": spec.render_version,
        "finding_ids": list(spec.finding_ids),
        "measurement_ids": list(spec.measurement_ids),
        "model": model_name or spec.model,
        "instructions": spec.instructions,
        "usage": usage,
        "status": status,
        "error": error,
        "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    try:
        messages = (
            run_result.all_messages() if run_result is not None else list(input_messages)
        )
        payload_text = _e5_serialize_agent_messages(messages, spec.image_refs)
        record = {**meta, "messages": json.loads(payload_text)}
        record_text = json.dumps(record, ensure_ascii=False)
        assert_no_signed_strings(record_text)
        for forbidden in ("Authorization", "X-Amz-Security-Token", "api_key"):
            assert forbidden not in record_text, f"audit artifact contains {forbidden!r}"
    except Exception as withhold:
        # Fail closed WITHOUT repeating potentially sensitive instructions,
        # provider errors, or message content in the fallback record.
        record = {
            **meta,
            "instructions": None,
            "messages": None,
            "status": "withheld",
            "error": None,
            "withhold_reason": (
                "audit content withheld: serialization or secret/signed-URL "
                "check failed"
            ),
            "withhold_exception_type": type(withhold).__name__,
        }
    (trace.out_dir / "agent_messages.jsonl").open("a", encoding="utf-8").write(
        json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
    )
    trace.add(
        agent=spec.agent,
        phase=spec.phase,
        action="agent_call",
        output={
            "call_id": call_id,
            "status": record["status"],
            "model": spec.model,
            "render_version": spec.render_version,
            "finding_ids": list(spec.finding_ids),
        },
        persist_output=True,
    )
    return call_id
