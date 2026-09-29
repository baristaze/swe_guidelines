"""The idempotency records table.

Revision ID: 202609160958
Revises: 202609160233
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202609160958"
down_revision = "202609160233"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609160958_idempotency_records.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609160958_idempotency_records.down.sql")
