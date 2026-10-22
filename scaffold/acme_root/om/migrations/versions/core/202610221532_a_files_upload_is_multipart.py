"""A file's upload is multipart: its row names the store's upload id, which
every part, the resume, and the complete name.

Revision ID: 202610221532
Revises: 202610142241
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610221532"
down_revision = "202610142241"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610221532_a_files_upload_is_multipart.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610221532_a_files_upload_is_multipart.down.sql")
