"""A membership is soft-deletable: a removed member's membership ends with
them instead of staying live for the retention period.

Revision ID: 202609192100
Revises: 202609191600
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202609192100"
down_revision = "202609192001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609192100_memberships_deleted.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609192100_memberships_deleted.down.sql")
