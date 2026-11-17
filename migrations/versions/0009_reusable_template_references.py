"""Recorded reusable approved-template references (ADR 0009).

Additive and defaulted: existing ``layout_proof_approvals`` rows keep their
artifact-scoped meaning, carry no identity and are never treated as reusable.
No backfill. The reference only points at an approval that still exists, so the
downgrade drops this table and nothing else.
"""

from alembic import op
import sqlalchemy as sa


revision = "0009_reusable_template_references"
down_revision = "0008_target_job_operations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reusable_template_references",
        sa.Column("scope_id", sa.Text(), primary_key=True),
        sa.Column("identity_sha256", sa.Text(), primary_key=True),
        sa.Column("artifact_id", sa.Text(), nullable=False),
        sa.Column("approval_id", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("body_sha256", sa.Text(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.clock_timestamp()),
        sa.ForeignKeyConstraint(
            ["artifact_id", "approval_id"],
            ["layout_proof_approvals.artifact_id", "layout_proof_approvals.approval_id"],
            name="fk_reusable_reference_approval", ondelete="CASCADE",
        ),
        sa.CheckConstraint("scope_id = 'local-synthetic'", name="ck_reusable_reference_scope"),
        sa.CheckConstraint("identity_sha256 ~ '^[0-9a-f]{64}$' AND body_sha256 ~ '^[0-9a-f]{64}$'",
                           name="ck_reusable_reference_checksums"),
        sa.CheckConstraint("jsonb_typeof(body::jsonb) = 'object'", name="ck_reusable_reference_body"),
    )
    op.create_index("ix_reusable_reference_scope", "reusable_template_references", ["scope_id", "recorded_at"])


def downgrade() -> None:
    op.drop_table("reusable_template_references")
