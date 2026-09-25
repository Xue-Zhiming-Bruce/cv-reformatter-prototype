from enum import Enum
from pathlib import Path


class WorkflowLane(str, Enum):
    PDF = "pdf"
    DOCX = "docx"


def workflow_lane_for_filename(filename: str) -> WorkflowLane:
    try:
        return WorkflowLane(Path(filename).suffix.lower().removeprefix("."))
    except ValueError as exc:
        raise ValueError("Only .pdf and .docx files select a supported workflow lane.") from exc


def require_same_lane(
    candidate_lane: WorkflowLane,
    other_lane: WorkflowLane,
    *,
    other_name: str,
) -> None:
    if candidate_lane != other_lane:
        raise ValueError(
            f"{candidate_lane.value.upper()} candidates require {candidate_lane.value.upper()} "
            f"{other_name}; {other_lane.value.upper()} is unsupported."
        )
