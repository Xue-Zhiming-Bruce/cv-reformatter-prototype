"""Reference stable per-attempt output bundles without rewriting legacy rows."""

from alembic import op
import sqlalchemy as sa


revision = "0007_generation_bundles"
down_revision = "0006_workflow_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Explicit comparisons retain the same bounds with stable pg_dump/restore
    # constraint text; PostgreSQL reparses compound BETWEEN expressions differently.
    op.drop_constraint("ck_job_bounds", "workflow_jobs", type_="check")
    op.create_check_constraint("ck_job_bounds", "workflow_jobs",
        "progress >= 0 AND progress <= 100 AND max_attempts >= 1 AND max_attempts <= 3 AND attempt_count >= 0 AND attempt_count <= max_attempts")
    op.add_column("generation_versions", sa.Column("bundle_path", sa.Text()))
    op.create_check_constraint("ck_generation_bundle_path", "generation_versions",
        "bundle_path IS NULL OR bundle_path ~ '^jobs/job_[0-9a-f]{32}/attempt_[0-9]{4}/[a-zA-Z0-9_-]{1,100}$'")


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM generation_versions WHERE bundle_path IS NOT NULL)")):
        raise RuntimeError("Published bundle references cannot be dropped; restore a matching application/database/artifact backup for rollback.")
    op.drop_constraint("ck_generation_bundle_path", "generation_versions", type_="check")
    op.drop_column("generation_versions", "bundle_path")
    op.drop_constraint("ck_job_bounds", "workflow_jobs", type_="check")
    op.create_check_constraint("ck_job_bounds", "workflow_jobs",
        "progress BETWEEN 0 AND 100 AND max_attempts BETWEEN 1 AND 3 AND attempt_count BETWEEN 0 AND max_attempts")
