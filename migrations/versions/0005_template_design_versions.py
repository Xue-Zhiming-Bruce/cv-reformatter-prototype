"""Persist immutable template design lifecycle history."""

from alembic import op
import sqlalchemy as sa


revision = "0005_template_design_versions"
down_revision = "0004_layout_proof_approvals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "template_design_versions",
        sa.Column("event_id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("artifact_id", sa.Text(), nullable=False),
        sa.Column("design_id", sa.Text(), nullable=False),
        sa.Column("profile_revision_id", sa.Text(), nullable=False),
        sa.Column("profile_sha256", sa.Text(), nullable=False),
        sa.Column("target_sha256", sa.Text(), nullable=False),
        sa.Column("inventory_sha256", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("body_sha256", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.clock_timestamp()),
        sa.ForeignKeyConstraint(["artifact_id", "profile_revision_id"],
            ["candidate_profile_revisions.artifact_id", "candidate_profile_revisions.profile_revision_id"], ondelete="CASCADE"),
        sa.CheckConstraint("state IN ('draft', 'ready', 'ready_with_review', 'unsupported', 'provider_failed', 'invalid_proposal', 'fallback_requires_approval', 'approved', 'superseded')", name="ck_design_version_state"),
        sa.CheckConstraint("profile_sha256 ~ '^[0-9a-f]{64}$' AND target_sha256 ~ '^[0-9a-f]{64}$' AND inventory_sha256 ~ '^[0-9a-f]{64}$' AND body_sha256 ~ '^[0-9a-f]{64}$'", name="ck_design_version_checksums"),
        sa.CheckConstraint("jsonb_typeof(body::jsonb) = 'object'", name="ck_design_version_body"),
        sa.CheckConstraint("state NOT IN ('approved', 'superseded') OR (body::jsonb->>'approved_at' IS NOT NULL AND body::jsonb->>'layout_spec_json' IS NOT NULL)", name="ck_design_version_approval"),
    )
    op.create_index("ix_design_version_current", "template_design_versions", ["artifact_id", "design_id", "event_id"])
    op.execute("""CREATE TRIGGER immutable_template_design_version BEFORE UPDATE ON template_design_versions
        FOR EACH ROW EXECUTE FUNCTION reject_generation_record_update()""")


def downgrade() -> None:
    op.drop_table("template_design_versions")
