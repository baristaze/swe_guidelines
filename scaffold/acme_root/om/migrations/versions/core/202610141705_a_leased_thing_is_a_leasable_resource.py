"""A leased thing is a leasable resource: `core.resources` becomes
`core.leasable_resources`, and `resource_id` on the leases and the requests
becomes `leasable_resource_id`, every row kept; the park reason `resource` and
the outbox kinds `leases.resource.*` move to their new names in place.

Revision ID: 202610141705
Revises: 202610141701
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610141705"
down_revision = "202610141701"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610141705_a_leased_thing_is_a_leasable_resource.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610141705_a_leased_thing_is_a_leasable_resource.down.sql")
