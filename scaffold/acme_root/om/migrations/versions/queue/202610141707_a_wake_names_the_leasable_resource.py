"""A queued wake names the leasable resource: a `WAKE_PARKED` item whose
reason is `resource` carries `leasable_resource`, in place.

Revision ID: 202610141707
Revises: 202609280003
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610141707"
down_revision = "202609280003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.QUEUE, "202610141707_a_wake_names_the_leasable_resource.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.QUEUE, "202610141707_a_wake_names_the_leasable_resource.down.sql")
