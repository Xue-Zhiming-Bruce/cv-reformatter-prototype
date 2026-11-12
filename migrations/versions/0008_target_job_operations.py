"""Uploaded-target job operations, bounded progress detail and proof results."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "0008_target_job_operations"
down_revision = "0007_generation_bundles"
branch_labels = None
depends_on = None

NEW_OPERATIONS = ("create_proofs", "generate_target")


def upgrade() -> None:
    op.drop_constraint("ck_job_local_scope", "workflow_jobs", type_="check")
    op.create_check_constraint("ck_job_local_scope", "workflow_jobs",
        "scope_id = 'local-synthetic' AND operation IN ('generate', 'create_proofs', 'generate_target')")
    op.add_column("workflow_jobs", sa.Column("progress_detail", JSONB))
    op.create_check_constraint("ck_job_progress_detail", "workflow_jobs",
        "progress_detail IS NULL OR jsonb_typeof(progress_detail) = 'object'")
    op.add_column("workflow_jobs", sa.Column("result_proof_approval_id", sa.Text()))
    op.create_foreign_key("fk_job_result_proof", "workflow_jobs", "layout_proof_approvals",
        ["artifact_id", "result_proof_approval_id"], ["artifact_id", "approval_id"])
    # Explicit comparisons keep pg_dump/restore constraint text stable.
    op.drop_constraint("ck_job_result", "workflow_jobs", type_="check")
    op.create_check_constraint("ck_job_result", "workflow_jobs",
        "(state = 'succeeded') = (result_generation_id IS NOT NULL OR result_proof_approval_id IS NOT NULL) "
        "AND (state != 'succeeded' OR progress = 100)")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.scalar(sa.text(
        "SELECT EXISTS (SELECT 1 FROM workflow_jobs WHERE operation IN ('create_proofs', 'generate_target'))"
    )):
        raise RuntimeError(
            "Uploaded-target job history cannot be dropped; restore a matching application/database/artifact "
            "backup for rollback."
        )
    op.drop_constraint("ck_job_result", "workflow_jobs", type_="check")
    op.create_check_constraint("ck_job_result", "workflow_jobs",
        "(state = 'succeeded') = (result_generation_id IS NOT NULL) AND (state != 'succeeded' OR progress = 100)")
    op.drop_constraint("fk_job_result_proof", "workflow_jobs", type_="foreignkey")
    op.drop_column("workflow_jobs", "result_proof_approval_id")
    op.drop_constraint("ck_job_progress_detail", "workflow_jobs", type_="check")
    op.drop_column("workflow_jobs", "progress_detail")
    op.drop_constraint("ck_job_local_scope", "workflow_jobs", type_="check")
    op.create_check_constraint("ck_job_local_scope", "workflow_jobs",
        "scope_id = 'local-synthetic' AND operation = 'generate'")
