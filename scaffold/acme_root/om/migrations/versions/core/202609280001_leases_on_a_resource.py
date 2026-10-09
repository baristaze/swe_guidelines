"""Leases on a resource: the resources with their anchors, the leases, and
the requests in line, each with the tenant's fence.

Revision ID: 202609280001
Revises: 202609280000
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202609280001"
down_revision = "202609280000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609280001_leases_on_a_resource.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609280001_leases_on_a_resource.down.sql")
