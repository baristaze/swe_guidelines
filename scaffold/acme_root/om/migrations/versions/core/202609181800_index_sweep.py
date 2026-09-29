"""Drop the org_id indexes the primary key and the new live-identity index
already serve; add that index.

Revision ID: 202609181800
Revises: 202609181201
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202609181800"
down_revision = "202609181201"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609181800_index_sweep.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609181800_index_sweep.down.sql")
