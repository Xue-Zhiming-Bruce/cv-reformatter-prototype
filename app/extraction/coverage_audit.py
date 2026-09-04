from __future__ import annotations

from collections import Counter
from typing import Iterable, Literal

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
    status: Literal["passed", "failed", "not_run"]
    source_line_count: int = 0
    output_line_count: int = 0
    matched_line_count: int = 0
    missing_lines: list[CoverageMismatch] = Field(default_factory=list)
    invented_or_altered_lines: list[CoverageMismatch] = Field(default_factory=list)
    detail: str | None = None

    @property
    def approval_blocked(self) -> bool:
        return self.status == "failed"


def normalize_audit_line(line: str) -> str:
    return " ".join(line.replace("\u2022", "•").split()).strip()


def audit_segmentation_lines(
    source_text: str,
    output_lines: Iterable[str | tuple[str, str]],
) -> SegmentationCoverageAudit:
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
    return SegmentationCoverageAudit(
        status="failed" if missing or extra else "passed",
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
