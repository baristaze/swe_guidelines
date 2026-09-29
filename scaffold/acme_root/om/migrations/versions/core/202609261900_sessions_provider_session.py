"""Sessions: the identity provider's own session behind each, which the
sign-out ends too. Expand only: one nullable column.

Revision ID: 202609261900
Revises: 202609261701
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202609261900"
down_revision = "202609261701"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609261900_sessions_provider_session.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609261900_sessions_provider_session.down.sql")
