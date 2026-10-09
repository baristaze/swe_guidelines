"""The leases listed newest first: the org's, and one resource's, each by an
index of its own.

Revision ID: 202610091856
Revises: 202609280002
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202610091856"
down_revision = "202609280002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610091856_leases_listed_newest_first.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202610091856_leases_listed_newest_first.down.sql")
