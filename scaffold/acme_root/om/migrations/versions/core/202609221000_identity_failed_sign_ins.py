"""An identity counts its run of failed sign-ins, and when the last one was,
so the sign-in delay grows from storage the tenancy role owns.

Revision ID: 202609221000
Revises: 202609212000
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202609221000"
down_revision = "202609212000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609221000_identity_failed_sign_ins.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609221000_identity_failed_sign_ins.down.sql")
