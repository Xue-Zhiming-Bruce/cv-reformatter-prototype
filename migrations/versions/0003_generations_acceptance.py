"""Persist immutable generation/template provenance and exact-output acceptance."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0003_generations_acceptance"
down_revision = "0002_profile_revisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "generation_versions",
        sa.Column("artifact_id", sa.Text(), primary_key=True),
        sa.Column("generation_id", sa.Text(), primary_key=True),
        sa.Column("profile_revision_id", sa.Text(), nullable=False),
        sa.Column("manifest_sha256", sa.Text(), nullable=False),
        sa.Column("manifest", postgresql.JSONB(), nullable=False),
        sa.Column("layout_spec", postgresql.JSONB(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.clock_timestamp()),
        sa.ForeignKeyConstraint(["artifact_id", "profile_revision_id"],
            ["candidate_profile_revisions.artifact_id", "candidate_profile_revisions.profile_revision_id"], ondelete="CASCADE"),
        sa.CheckConstraint("manifest_sha256 ~ '^[0-9a-f]{64}$'", name="ck_generation_manifest_sha256"),
        sa.CheckConstraint("jsonb_typeof(manifest) = 'object' AND jsonb_typeof(layout_spec) = 'object'", name="ck_generation_objects"),
    )
    op.add_column("artifact_sessions", sa.Column("current_generation_id", sa.Text()))
    op.create_foreign_key("fk_artifact_current_generation", "artifact_sessions", "generation_versions",
        ["artifact_id", "current_generation_id"], ["artifact_id", "generation_id"], deferrable=True, initially="DEFERRED")
    op.create_table(
        "final_output_acceptances",
        sa.Column("acceptance_id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("artifact_id", sa.Text(), nullable=False),
        sa.Column("generation_id", sa.Text(), nullable=False),
        sa.Column("body", postgresql.JSONB(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.clock_timestamp()),
        sa.ForeignKeyConstraint(["artifact_id", "generation_id"],
            ["generation_versions.artifact_id", "generation_versions.generation_id"], ondelete="CASCADE"),
        sa.CheckConstraint("jsonb_typeof(body) = 'object'", name="ck_acceptance_body_object"),
    )
    op.create_index("ix_final_acceptance_artifact", "final_output_acceptances", ["artifact_id", "acceptance_id"])
    op.execute("""CREATE FUNCTION reject_generation_record_update() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'Generation and acceptance records are immutable' USING ERRCODE = '23514';
        END;
        $$""")
    for table in ("generation_versions", "final_output_acceptances"):
        op.execute(f"CREATE TRIGGER immutable_{table} BEFORE UPDATE ON {table} "
                   "FOR EACH ROW EXECUTE FUNCTION reject_generation_record_update()")


def downgrade() -> None:
    op.drop_table("final_output_acceptances")
    op.drop_constraint("fk_artifact_current_generation", "artifact_sessions", type_="foreignkey")
    op.drop_column("artifact_sessions", "current_generation_id")
    op.drop_table("generation_versions")
    op.execute("DROP FUNCTION reject_generation_record_update()")
