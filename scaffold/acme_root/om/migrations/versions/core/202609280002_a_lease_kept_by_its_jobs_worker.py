"""A lease kept by the worker that runs its job: the job's key and its start
on a lease, and its window on a request.

Revision ID: 202609280002
Revises: 202609280001
"""

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202609280002"
down_revision = "202609280001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609280002_a_lease_kept_by_its_jobs_worker.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202609280002_a_lease_kept_by_its_jobs_worker.down.sql")
