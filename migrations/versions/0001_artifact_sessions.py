"""Persist artifact metadata without storing document bytes.

Revision ID: 0001_artifact_sessions
Revises: none
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0001_artifact_sessions"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "artifact_sessions",
        sa.Column("artifact_id", sa.Text(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("artifact_dir", sa.Text(), nullable=False),
        *(sa.Column(name, sa.Text()) for name in (
            "source_filename", "source_file_type", "original_pdf_preview_url", "original_preview_error",
            "target_format_role", "target_format_filename", "target_format_download_url", "template_name",
            "html_surface_url", "pdf_download_url", "pdf_preview_url",
        )),
        sa.Column("blind_profile", sa.Boolean()),
        sa.Column("needs_review_count", sa.Integer()),
        sa.Column("missing_field_labels", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("debug_artifacts", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.CheckConstraint("needs_review_count IS NULL OR needs_review_count >= 0", name="ck_artifact_review_count"),
        sa.CheckConstraint("jsonb_typeof(missing_field_labels) = 'array'", name="ck_artifact_missing_labels"),
        sa.CheckConstraint("jsonb_typeof(debug_artifacts) = 'object'", name="ck_artifact_debug_object"),
    )
    op.create_index("ix_artifact_sessions_updated", "artifact_sessions", ["updated_at", "artifact_id"])


def downgrade() -> None:
    op.drop_index("ix_artifact_sessions_updated", table_name="artifact_sessions")
    op.drop_table("artifact_sessions")
