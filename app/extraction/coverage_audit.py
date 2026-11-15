from __future__ import annotations

from collections import Counter
from typing import Iterable, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field


class CoverageMismatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    line: str
    count: int = Field(gt=0)
    source_line_numbers: list[int] = Field(default_factory=list)
    output_block_ids: list[str] = Field(default_factory=list)


class SegmentationCoverageAudit(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal["segmentation-audit/1"] = "segmentation-audit/1"
    #: ``incomplete`` means source text the reader could not read was left out
    #: before segmentation, so line coverage of the supplied text proves nothing
    #: about it.
    status: Literal["passed", "failed", "not_run", "incomplete"]
    source_line_count: int = 0
    output_line_count: int = 0
    matched_line_count: int = 0
    missing_lines: list[CoverageMismatch] = Field(default_factory=list)
    invented_or_altered_lines: list[CoverageMismatch] = Field(default_factory=list)
    #: Everything the reader reported, for recruiter review.
    unread_source_content: list[str] = Field(default_factory=list)
    #: The subset where visible text was lost; only these keep the audit off
    #: ``passed`` and block approval. ``None`` marks an audit written before the
    #: split, where the whole reported list has to count as lost text.
    unread_text_source_content: list[str] | None = None
    detail: str | None = None

    @property
    def approval_blocked(self) -> bool:
        return self.status == "failed"

    @property
    def unread_text(self) -> list[str]:
        """Reported content whose text was lost, legacy audits included."""
        if self.unread_text_source_content is None:
            return list(self.unread_source_content)
        return self.unread_text_source_content


def normalize_audit_line(line: str) -> str:
    return " ".join(line.replace("\u2022", "•").split()).strip()


def _unread_detail(unread: Sequence[str], unread_text: Sequence[str]) -> str | None:
    """One sentence naming what the reader reported, text losses first."""
    if unread_text:
        return (
            "Source text the reader could not read is missing from this audit: "
            + " ".join(unread_text)
        )
    if unread:
        return (
            "The reader also reported content it does not read and that carries no "
            "text for this audit: " + " ".join(unread)
        )
    return None


def audit_segmentation_lines(
    source_text: str,
    output_lines: Iterable[str | tuple[str, str]],
    unread_source_content: Iterable[str] = (),
    unread_text_source_content: Iterable[str] = (),
) -> SegmentationCoverageAudit:
    """Audit that segmentation kept every supplied source line.

    ``unread_source_content`` names source content the ingestion reader reported
    and never supplied, so an audit that only compared the supplied lines would
    call such a document fully covered; every entry is recorded for review.
    ``unread_text_source_content`` is the subset where visible text was lost:
    only that keeps the status off ``passed``, because reported content that
    carries no text (an image) is not a text omission.
    """
    unread = [item for item in unread_source_content if item.strip()]
    unread_text = [item for item in unread_text_source_content if item.strip()]
    source = [
        normalized
        for line in source_text.splitlines()
        if (normalized := normalize_audit_line(line))
    ]
    output: list[str] = []
    output_blocks: dict[str, list[str]] = {}
    for item in output_lines:
        line, block_id = item if isinstance(item, tuple) else (item, None)
        normalized = normalize_audit_line(line)
        if not normalized:
            continue
        output.append(normalized)
        if block_id is not None:
            output_blocks.setdefault(normalized, []).append(block_id)
    source_counts = Counter(source)
    output_counts = Counter(output)
    missing = source_counts - output_counts
    extra = output_counts - source_counts
    status: Literal["failed", "incomplete", "passed"] = (
        "failed" if missing or extra else "incomplete" if unread_text else "passed"
    )
    return SegmentationCoverageAudit(
        status=status,
        unread_source_content=unread,
        unread_text_source_content=unread_text,
        detail=_unread_detail(unread, unread_text),
        source_line_count=len(source),
        output_line_count=len(output),
        matched_line_count=len(source) - sum(missing.values()),
        missing_lines=[
            CoverageMismatch(
                line=line,
                count=count,
                source_line_numbers=[
                    index
                    for index, source_line in enumerate(source, start=1)
                    if source_line == line
                ],
            )
            for line, count in missing.items()
        ],
        invented_or_altered_lines=[
            CoverageMismatch(
                line=line,
                count=count,
                output_block_ids=sorted(set(output_blocks.get(line, []))),
            )
            for line, count in extra.items()
        ],
    )
