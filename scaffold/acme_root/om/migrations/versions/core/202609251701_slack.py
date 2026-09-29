"""Slack: the channel an org connects, the one-time codes that connect one,
and the record of what was posted there.

Revision ID: 202609251701
Revises: 202609251700
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202609251701"
down_revision = "202609251700"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609251701_slack.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609251701_slack.down.sql")
