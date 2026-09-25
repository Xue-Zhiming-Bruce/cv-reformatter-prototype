import pytest

from app.workflow import WorkflowLane, require_same_lane, workflow_lane_for_filename


def test_workflow_lane_is_derived_and_mismatches_fail_closed() -> None:
    assert workflow_lane_for_filename("resume.PDF") == WorkflowLane.PDF
    assert workflow_lane_for_filename("resume.docx") == WorkflowLane.DOCX
    with pytest.raises(ValueError, match="PDF candidates require PDF outputs"):
        require_same_lane(WorkflowLane.PDF, WorkflowLane.DOCX, other_name="outputs")
    with pytest.raises(ValueError, match="Only .pdf and .docx"):
        workflow_lane_for_filename("resume.txt")
