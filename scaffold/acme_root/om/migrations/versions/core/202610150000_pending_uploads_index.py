"""The index of pending uploads holds them alone: the files purge names their
status as a literal, so a generic plan proves the predicate, and a confirm
writes the index no entry.

Revision ID: 202610150000
Revises: 202610140000
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610150000"
down_revision = "202610140000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610150000_pending_uploads_index.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610150000_pending_uploads_index.down.sql")
