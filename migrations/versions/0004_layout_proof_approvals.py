"""Persist immutable layout proof approval and rejection history."""

from alembic import op
import sqlalchemy as sa


revision = "0004_layout_proof_approvals"
down_revision = "0003_generations_acceptance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "layout_proof_approvals",
        sa.Column("artifact_id", sa.Text(), sa.ForeignKey("artifact_sessions.artifact_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("approval_id", sa.Text(), primary_key=True),
        sa.Column("ordinal", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("target_sha256", sa.Text(), nullable=False),
        sa.Column("layout_sha256", sa.Text(), nullable=False),
        sa.Column("renderer_version", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("body_sha256", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.clock_timestamp()),
        sa.CheckConstraint("state IN ('pending', 'approved', 'rejected')", name="ck_proof_approval_state"),
        sa.CheckConstraint("target_sha256 ~ '^[0-9a-f]{64}$' AND layout_sha256 ~ '^[0-9a-f]{64}$' AND body_sha256 ~ '^[0-9a-f]{64}$'", name="ck_proof_approval_checksums"),
        sa.CheckConstraint("jsonb_typeof(body::jsonb) = 'object'", name="ck_proof_approval_body"),
    )
    op.create_index("ix_proof_approval_current", "layout_proof_approvals", ["artifact_id", "ordinal"])
    op.execute("""CREATE TRIGGER immutable_layout_proof_approval BEFORE UPDATE ON layout_proof_approvals
        FOR EACH ROW EXECUTE FUNCTION reject_generation_record_update()""")


def downgrade() -> None:
    op.drop_table("layout_proof_approvals")
