"""An auth session's change events read under its kind: the events of kind
`tenancy.session.*` become `tenancy.auth_session.*` in place, each row kept.

Revision ID: 202610141702
Revises: 202609280001
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610141702"
down_revision = "202609280001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.ACTIVITY, "202610141702_an_auth_sessions_events_keep_its_kind.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.ACTIVITY, "202610141702_an_auth_sessions_events_keep_its_kind.down.sql")
