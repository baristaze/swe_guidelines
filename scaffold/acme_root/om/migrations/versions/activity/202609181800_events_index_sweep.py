"""Drop the (org_id, id) index the primary key already serves.

Revision ID: 202609181800
Revises: 202609181203
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202609181800"
down_revision = "202609181203"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.ACTIVITY, "202609181800_events_index_sweep.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.ACTIVITY, "202609181800_events_index_sweep.down.sql")
