"""The work queue table.

Revision ID: 202609160223
Revises: None
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202609160223"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.QUEUE, "202609160223_work_items.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.QUEUE, "202609160223_work_items.down.sql")
