"""Persist immutable candidate revisions and an artifact-scoped current pointer."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0002_profile_revisions"
down_revision = "0001_artifact_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "candidate_profile_revisions",
        sa.Column("artifact_id", sa.Text(), sa.ForeignKey("artifact_sessions.artifact_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("profile_revision_id", sa.Text(), primary_key=True),
        sa.Column("profile_sha256", sa.Text(), nullable=False),
        sa.Column("source_draft_sha256", sa.Text(), nullable=False),
        sa.Column("body", postgresql.JSONB(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.clock_timestamp()),
        sa.CheckConstraint("profile_sha256 ~ '^[0-9a-f]{64}$'", name="ck_revision_profile_sha256"),
        sa.CheckConstraint("source_draft_sha256 ~ '^[0-9a-f]{64}$'", name="ck_revision_source_sha256"),
        sa.CheckConstraint("jsonb_typeof(body) = 'object'", name="ck_revision_body_object"),
    )
    op.add_column("artifact_sessions", sa.Column("current_profile_revision_id", sa.Text()))
    op.create_foreign_key("fk_artifact_current_revision", "artifact_sessions", "candidate_profile_revisions",
        ["artifact_id", "current_profile_revision_id"], ["artifact_id", "profile_revision_id"],
        deferrable=True, initially="DEFERRED")
    op.execute("""CREATE FUNCTION reject_profile_revision_update() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'Candidate profile revisions are immutable' USING ERRCODE = '23514';
        END;
        $$""")
    op.execute("""CREATE TRIGGER immutable_profile_revision BEFORE UPDATE ON candidate_profile_revisions
        FOR EACH ROW EXECUTE FUNCTION reject_profile_revision_update()""")


def downgrade() -> None:
    op.drop_constraint("fk_artifact_current_revision", "artifact_sessions", type_="foreignkey")
    op.drop_column("artifact_sessions", "current_profile_revision_id")
    op.drop_table("candidate_profile_revisions")
    op.execute("DROP FUNCTION reject_profile_revision_update()")
