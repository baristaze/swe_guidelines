"""The stream's seq is a bigint, like the cursor head it is taken from.

Revision ID: 202609200930
Revises: 202609192054
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202609200930"
down_revision = "202609192054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.ACTIVITY, "202609200930_events_seq_bigint.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.ACTIVITY, "202609200930_events_seq_bigint.down.sql")
