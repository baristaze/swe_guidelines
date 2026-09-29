"""The sweep's indexes: a plain (org_id, deleted_at) on users and memberships,
since the unique ones beside them are among the living and the purge reads the
rows they leave out.

Revision ID: 202609201400
Revises: 202609192300
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202609201400"
down_revision = "202609192300"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609201400_sweep_indexes.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609201400_sweep_indexes.down.sql")
