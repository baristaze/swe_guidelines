"""The operator allowlist entry carries a role, `read` or `write`, in place
of the flag. A rename in one migration: ADR 0006 allows it before the first
deployment.

Revision ID: 202609202100
Revises: 202609202000
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202609202100"
down_revision = "202609202000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609202100_operator_role.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609202100_operator_role.down.sql")
