"""The claim walks the ready items in the order they became ready.

Revision ID: 202610060000
Revises: 202610050000
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610060000"
down_revision = "202610050000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.QUEUE, "202610060000_claim_by_readiness.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.QUEUE, "202610060000_claim_by_readiness.down.sql")
