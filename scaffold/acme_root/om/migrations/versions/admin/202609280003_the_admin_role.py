"""The admin role, whole: the platform's size as the sweep last counted it, and
the serving logins' grants.

Revision ID: 202609280003
Revises: None
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202609280003"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.ADMIN, "202609280003_the_admin_role.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.ADMIN, "202609280003_the_admin_role.down.sql")
