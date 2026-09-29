"""The idempotency record carries the id the create uses, minted before the
marker, so a retry that takes over an abandoned marker creates on the same id.

Revision ID: 202609191500
Revises: 202609181800
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202609191500"
down_revision = "202609181800"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609191500_idempotency_target_id.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609191500_idempotency_target_id.down.sql")
