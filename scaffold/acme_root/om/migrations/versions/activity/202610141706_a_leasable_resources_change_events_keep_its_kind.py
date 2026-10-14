"""A leasable resource's change events read under its kind: the events of kind
`leases.resource.*` become `leases.leasable_resource.*` in place.

Revision ID: 202610141706
Revises: 202610141703
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610141706"
down_revision = "202610141703"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(
        DatabaseRole.ACTIVITY,
        "202610141706_a_leasable_resources_change_events_keep_its_kind.up.sql",
    )


def downgrade() -> None:
    run_sql(
        DatabaseRole.ACTIVITY,
        "202610141706_a_leasable_resources_change_events_keep_its_kind.down.sql",
    )
