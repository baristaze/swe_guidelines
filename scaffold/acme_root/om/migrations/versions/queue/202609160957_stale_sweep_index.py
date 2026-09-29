"""The sweep's compound index on work_items.

Revision ID: 202609160957
Revises: 202609160223
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202609160957"
down_revision = "202609160223"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.QUEUE, "202609160957_stale_sweep_index.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.QUEUE, "202609160957_stale_sweep_index.down.sql")
