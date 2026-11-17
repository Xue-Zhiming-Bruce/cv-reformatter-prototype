"""Conversion execution envelopes and their append-only event history (ADR 0010).

Additive only: two new tables, no column added to an existing table, no
backfill. A pre-existing artifact opens its envelope at its next covered stage,
so earlier spend is recorded as absent, never estimated retroactively.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "0010_conversion_envelopes"
down_revision = "0009_reusable_references"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversion_envelopes",
        sa.Column("conversion_id", sa.Text(), primary_key=True),
        sa.Column("artifact_id", sa.Text(), nullable=False),
        sa.Column("scope_id", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("limits", JSONB, nullable=False),
        sa.Column("protected_model_requests", sa.Integer(), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.clock_timestamp()),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("supersedes_conversion_id", sa.Text()),
        sa.CheckConstraint("conversion_id ~ '^env_[0-9a-f]{32}$'", name="ck_envelope_id"),
        sa.CheckConstraint("scope_id = 'local-synthetic'", name="ck_envelope_scope"),
        sa.CheckConstraint("state IN ('open', 'exhausted', 'closed', 'superseded')",
                           name="ck_envelope_state"),
        sa.CheckConstraint("jsonb_typeof(limits) = 'object'", name="ck_envelope_limits"),
        sa.CheckConstraint("protected_model_requests >= 0", name="ck_envelope_protected"),
    )
    op.create_index("ix_conversion_envelope_artifact", "conversion_envelopes",
                    ["artifact_id", "opened_at"])
    op.create_table(
        "conversion_envelope_events",
        sa.Column("conversion_id", sa.Text(), nullable=False),
        sa.Column("sequence", sa.Integer(), primary_key=True),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("dimension", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("provider_outcome", sa.Text(), nullable=False),
        sa.Column("observed", JSONB),
        sa.Column("job_id", sa.Text()),
        sa.Column("attempt_number", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.clock_timestamp()),
        sa.ForeignKeyConstraint(["conversion_id"], ["conversion_envelopes.conversion_id"],
                                ondelete="CASCADE", name="fk_envelope_event_envelope"),
        sa.CheckConstraint(
            "kind IN ('opened', 'reserved', 'consumed', 'settled_unknown', 'reused', 'refused', 'closed')",
            name="ck_envelope_event_kind"),
        sa.CheckConstraint(
            "dimension IN ('model_requests', 'tool_calls', 'analyzer_operations', 'deadline')",
            name="ck_envelope_event_dimension"),
        sa.CheckConstraint("amount >= 0", name="ck_envelope_event_amount"),
        sa.CheckConstraint("provider_outcome IN ('none_sent', 'confirmed', 'unknown')",
                           name="ck_envelope_event_outcome"),
        sa.CheckConstraint("observed IS NULL OR jsonb_typeof(observed) = 'object'",
                           name="ck_envelope_event_observed"),
        sa.CheckConstraint("attempt_number IS NULL OR attempt_number BETWEEN 1 AND 3",
                           name="ck_envelope_event_attempt"),
    )
    # A recorded reservation or settlement is history: it is never rewritten.
    op.execute("""CREATE TRIGGER append_only_conversion_envelope_event
        BEFORE UPDATE OR DELETE ON conversion_envelope_events
        FOR EACH ROW EXECUTE FUNCTION reject_generation_record_update()""")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.scalar(sa.text("SELECT EXISTS (SELECT 1 FROM conversion_envelopes)")):
        raise RuntimeError(
            "Conversion envelope history cannot be dropped: it is not reconstructable. Restore a "
            "matching application/database backup for rollback."
        )
    op.execute("DROP TRIGGER append_only_conversion_envelope_event ON conversion_envelope_events")
    op.drop_table("conversion_envelope_events")
    op.drop_index("ix_conversion_envelope_artifact", table_name="conversion_envelopes")
    op.drop_table("conversion_envelopes")
