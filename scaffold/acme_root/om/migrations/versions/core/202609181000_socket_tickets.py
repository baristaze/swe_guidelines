"""The socket tickets table.

Revision ID: 202609181000
Revises: 202609160958
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202609181000"
down_revision = "202609160958"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609181000_socket_tickets.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609181000_socket_tickets.down.sql")
