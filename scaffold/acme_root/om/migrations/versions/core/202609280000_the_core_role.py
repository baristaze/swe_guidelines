"""The core role, whole: tenancy, the outbox, the idempotency markers, media's
files, and the orchestrations, with every table's fence and the serving
logins' grants.

Revision ID: 202609280000
Revises: None
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202609280000"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609280000_the_core_role.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609280000_the_core_role.down.sql")
