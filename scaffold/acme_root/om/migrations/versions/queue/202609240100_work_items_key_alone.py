"""A work item's idempotency key is unique within its tenant and nowhere else.

Revision ID: 202609240100
Revises: 202609240000
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202609240100"
down_revision = "202609240000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.QUEUE, "202609240100_work_items_key_alone.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.QUEUE, "202609240100_work_items_key_alone.down.sql")
