"""The claim token on work items: minted by the claim, conditioned on by
every transition of a claimed item.

Revision ID: 202609192000
Revises: 202609181202
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202609192000"
down_revision = "202609181202"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.QUEUE, "202609192000_claim_token.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.QUEUE, "202609192000_claim_token.down.sql")
