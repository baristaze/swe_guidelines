"""The billing swimlane: an org's billing account and the processor's
deliveries already applied, each under the tenant fence.

Revision ID: 202609261400
Revises: 202609261300
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202609261400"
down_revision = "202609261300"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609261400_billing.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609261400_billing.down.sql")
