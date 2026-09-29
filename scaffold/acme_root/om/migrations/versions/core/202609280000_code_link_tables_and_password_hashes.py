"""The contract half of two expands: the code-linked Slack channel's two
tables go, and so do the password hashes. The three dead identity columns and
`tasks.remind_at` stay one more release, out of the mapping: the release
before this one still names them in its inserts.

Revision ID: 202609280000
Revises: 202609261900
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202609280000"
down_revision = "202609261900"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609280000_code_link_tables_and_password_hashes.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609280000_code_link_tables_and_password_hashes.down.sql")
