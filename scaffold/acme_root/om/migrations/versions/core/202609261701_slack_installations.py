"""Slack installations: the app an org installs into its workspace, and the
one-time states an install carries through Slack and back. The expand half:
the code-linked channel's two tables stay for the release before this one.

Revision ID: 202609261701
Revises: 202609261700
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202609261701"
down_revision = "202609261700"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609261701_slack_installations.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609261701_slack_installations.down.sql")
