"""updated_by on every Trackable table of the core role.

Revision ID: 202609181201
Revises: 202609181200
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202609181201"
down_revision = "202609181200"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609181201_updated_by.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609181201_updated_by.down.sql")
