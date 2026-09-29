"""Unique among the living: the org slug and the membership key become
partial unique indexes, so a deleted row frees its key.

Revision ID: 202609192300
Revises: 202609192201
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202609192300"
down_revision = "202609192201"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609192300_living_uniques.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609192300_living_uniques.down.sql")
