"""The idempotency key of a work item is unique within its tenant, and the
purge of settled items reads across tenants by status and last change.

Revision ID: 202609230000
Revises: 202609221201
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202609230000"
down_revision = "202609221201"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.QUEUE, "202609230000_work_items_key_per_tenant.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.QUEUE, "202609230000_work_items_key_per_tenant.down.sql")
