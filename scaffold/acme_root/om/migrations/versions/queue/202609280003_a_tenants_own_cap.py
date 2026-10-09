"""A tenant's own cap on a lane: one row per tenant and lane, under the
one policy every tenant table has.

Revision ID: 202609280003
Revises: 202609280002
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202609280003"
down_revision = "202609280002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.QUEUE, "202609280003_a_tenants_own_cap.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.QUEUE, "202609280003_a_tenants_own_cap.down.sql")
