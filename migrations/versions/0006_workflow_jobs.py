"""Local durable jobs, fenced leases and retained attempt history."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "0006_workflow_jobs"
down_revision = "0005_template_design_versions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workflow_jobs",
        sa.Column("job_id", sa.Text(), primary_key=True),
        sa.Column("artifact_id", sa.Text(), nullable=False),
        sa.Column("profile_revision_id", sa.Text(), nullable=False),
        sa.Column("scope_id", sa.Text(), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("request_sha256", sa.Text(), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("lease_token", sa.Text()),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.Text()),
        sa.Column("result_generation_id", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.clock_timestamp()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.clock_timestamp()),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["artifact_id", "profile_revision_id"],
            ["candidate_profile_revisions.artifact_id", "candidate_profile_revisions.profile_revision_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["artifact_id", "result_generation_id"],
            ["generation_versions.artifact_id", "generation_versions.generation_id"]),
        sa.UniqueConstraint("scope_id", "artifact_id", "operation", "idempotency_key", name="uq_workflow_job_request"),
        sa.CheckConstraint("scope_id = 'local-synthetic' AND operation = 'generate'", name="ck_job_local_scope"),
        sa.CheckConstraint("state IN ('queued', 'running', 'succeeded', 'failed', 'cancelled', 'needs_attention')", name="ck_job_state"),
        sa.CheckConstraint("progress BETWEEN 0 AND 100 AND max_attempts BETWEEN 1 AND 3 AND attempt_count BETWEEN 0 AND max_attempts", name="ck_job_bounds"),
        sa.CheckConstraint("(state = 'running') = (lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)", name="ck_job_lease"),
        sa.CheckConstraint("(state = 'succeeded') = (result_generation_id IS NOT NULL) AND (state != 'succeeded' OR progress = 100)", name="ck_job_result"),
        sa.CheckConstraint("request_sha256 ~ '^[0-9a-f]{64}$' AND jsonb_typeof(payload) = 'object'", name="ck_job_payload"),
    )
    op.create_index("ix_workflow_job_claim", "workflow_jobs", ["state", "created_at", "job_id"])
    op.create_table(
        "job_attempts",
        sa.Column("job_id", sa.Text(), sa.ForeignKey("workflow_jobs.job_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("attempt_number", sa.Integer(), primary_key=True),
        sa.Column("lease_token", sa.Text(), nullable=False, unique=True),
        sa.Column("bundle_path", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("error_code", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.clock_timestamp()),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("attempt_number BETWEEN 1 AND 3", name="ck_attempt_number"),
        sa.CheckConstraint("state IN ('running', 'abandoned', 'succeeded', 'failed', 'cancelled', 'needs_attention')", name="ck_attempt_state"),
        sa.CheckConstraint("(state = 'running') = (finished_at IS NULL)", name="ck_attempt_finished"),
    )
    op.execute("""CREATE FUNCTION guard_job_history() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_TABLE_NAME = 'workflow_jobs' THEN
            IF (NEW.job_id, NEW.created_at, NEW.artifact_id, NEW.profile_revision_id, NEW.scope_id, NEW.operation,
                NEW.idempotency_key, NEW.request_sha256, NEW.payload, NEW.max_attempts, NEW.deadline_at)
                IS DISTINCT FROM
               (OLD.job_id, OLD.created_at, OLD.artifact_id, OLD.profile_revision_id, OLD.scope_id, OLD.operation,
                OLD.idempotency_key, OLD.request_sha256, OLD.payload, OLD.max_attempts, OLD.deadline_at)
               OR OLD.state IN ('succeeded', 'failed', 'cancelled', 'needs_attention') THEN
                RAISE EXCEPTION 'Job request and terminal state are immutable' USING ERRCODE = '23514';
            END IF;
        ELSE
            IF (NEW.job_id, NEW.attempt_number, NEW.lease_token, NEW.bundle_path, NEW.started_at)
                IS DISTINCT FROM
               (OLD.job_id, OLD.attempt_number, OLD.lease_token, OLD.bundle_path, OLD.started_at)
               OR OLD.state != 'running' THEN
                RAISE EXCEPTION 'Completed attempt history is immutable' USING ERRCODE = '23514';
            END IF;
        END IF;
        RETURN NEW;
    END $$""")
    for table in ("workflow_jobs", "job_attempts"):
        op.execute(f"CREATE TRIGGER guard_{table}_history BEFORE UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION guard_job_history()")


def downgrade() -> None:
    op.drop_table("job_attempts")
    op.drop_table("workflow_jobs")
    op.execute("DROP FUNCTION guard_job_history()")
