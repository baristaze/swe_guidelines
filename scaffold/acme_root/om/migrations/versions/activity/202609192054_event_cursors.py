"""The cursor row per tenant that the append takes the next seq from.

Revision ID: 202609192054
Revises: 202609181800
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202609192054"
down_revision = "202609181800"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.ACTIVITY, "202609192054_event_cursors.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.ACTIVITY, "202609192054_event_cursors.down.sql")
