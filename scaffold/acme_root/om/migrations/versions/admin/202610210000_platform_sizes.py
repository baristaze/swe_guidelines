"""The platform's size as the sweep last counted it: the first table of the
admin role, the operator plane's own.

Revision ID: 202610210000
Revises: None
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610210000"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.ADMIN, "202610210000_platform_sizes.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.ADMIN, "202610210000_platform_sizes.down.sql")
