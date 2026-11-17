"""Key envelope events by their envelope, not by their number (ADR 0010).

``0010_conversion_envelopes`` made ``sequence`` the primary key on its own while
every writer allocates the next number per conversion, so the second envelope in
a database collided on ``sequence = 1``. The event key is now
``(conversion_id, sequence)``, which is what the repository model declares and
what the per-conversion allocation means. No column, row or trigger changes.
"""

from alembic import op
import sqlalchemy as sa


revision = "0011_envelope_event_sequence"
down_revision = "0010_conversion_envelopes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("conversion_envelope_events_pkey", "conversion_envelope_events",
                       type_="primary")
    op.create_primary_key("pk_conversion_envelope_event", "conversion_envelope_events",
                          ["conversion_id", "sequence"])


def downgrade() -> None:
    bind = op.get_bind()
    collisions = bind.scalar(sa.text(
        "SELECT count(*) FROM (SELECT sequence FROM conversion_envelope_events "
        "GROUP BY sequence HAVING count(*) > 1) AS collision"))
    if collisions:
        raise RuntimeError(
            "Envelope event numbers are per conversion and cannot be renumbered: the history is "
            "append-only. Restore a matching database backup for rollback."
        )
    op.drop_constraint("pk_conversion_envelope_event", "conversion_envelope_events",
                       type_="primary")
    op.create_primary_key("conversion_envelope_events_pkey", "conversion_envelope_events",
                          ["sequence"])
