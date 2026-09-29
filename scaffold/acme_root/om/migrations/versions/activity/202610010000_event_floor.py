"""The event cursor carries the stream's floor; the stream is indexed on when
each event was produced.

Revision ID: 202610010000
Revises: 202609240000
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610010000"
down_revision = "202609240000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.ACTIVITY, "202610010000_event_floor.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.ACTIVITY, "202610010000_event_floor.down.sql")
