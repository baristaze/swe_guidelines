"""`core.users` is narrowed on `identity_id` against `app.identity_id`, the
setting that names an identity, instead of `app.user_id`, which names a user.

Revision ID: 202609212000
Revises: 202609202100
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202609212000"
down_revision = "202609202100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609212000_users_narrowed_on_identity.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609212000_users_narrowed_on_identity.down.sql")
