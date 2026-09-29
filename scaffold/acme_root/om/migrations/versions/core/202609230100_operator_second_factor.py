"""The operator plane's second factor and token, a session's idle lifetime,
and the sign-in delay keyed on the email's digest in a table of its own.

Revision ID: 202609230100
Revises: 202609221201
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202609230100"
down_revision = "202609221201"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609230100_operator_second_factor.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609230100_operator_second_factor.down.sql")
