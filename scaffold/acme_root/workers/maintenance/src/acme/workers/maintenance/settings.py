"""The one settings object of the maintenance worker."""

import os
import socket

from pydantic import Field
from pydantic_settings import SettingsConfigDict

from acme.infra.impl.settings import InfraSettings
from acme.integrations.settings import IntegrationsSettings
from acme.om.storage.settings import StorageSettings


def default_worker_id() -> str:
    return f"maintenance-{socket.gethostname()}-{os.getpid()}"


class MaintenanceSettings(StorageSettings, InfraSettings, IntegrationsSettings):
    model_config = SettingsConfigDict(env_prefix="ACME_", env_file=".env", extra="ignore")

    service_name: str = "maintenance"
    version: str = "0.1.0"
    # The worker serves no HTTP but its metrics; containers bind 0.0.0.0.
    metrics_host: str = "127.0.0.1"
    metrics_port: int = 9464
    worker_id: str = Field(default_factory=default_worker_id)
    worker_lane: str = "default"
    # Every one of these is a count or a duration the loop divides or waits
    # on, so zero is not a smaller setting but a broken one: a lease of zero
    # cancels each item as `lease_lost` the moment it is claimed, a heartbeat
    # of zero never lets the claim loop run, and a capacity of zero claims
    # nothing at all. The process refuses to start instead.
    worker_capacity: int = Field(default=4, gt=0)
    worker_lease_seconds: int = Field(default=60, gt=0)
    worker_heartbeat_seconds: int = Field(default=10, gt=0)
    worker_sweep_seconds: int = Field(default=30, gt=0)
    worker_poll_seconds: int = Field(default=5, gt=0)
    # The sweep. A purge statement deletes a batch at most, so no statement
    # grows with a backlog past the database's statement deadline; the sweep
    # calls again while a batch comes back full. A pass stops taking tenants
    # at its budget and the next pass resumes at the tenant it stopped at.
    worker_purge_batch: int = Field(default=1000, gt=0)
    worker_sweep_budget_seconds: int = Field(default=20, gt=0)
    # How often the sweep counts the platform's size, the tally the operator
    # plane reads: a count across every tenant, so once an interval and not
    # every pass (ADR 0074).
    worker_tally_seconds: int = Field(default=300, gt=0)

    # How long each kind of row is kept after it stopped mattering, then
    # purged by the sweep. The outbox's must outlive the database's backups
    # (`backup_retention_days`, 7 by default in the database module), so a
    # role restored to an earlier point than its siblings is reconciled by
    # relaying the outbox again.
    outbox_retention_days: int = Field(default=8, gt=0)
    work_retention_days: int = Field(default=30, gt=0)
    idempotency_retention_hours: int = Field(default=24, gt=0)
    # Removed members, revoked keys, ended sessions, closed invitations, and
    # a deleted tenant's rows.
    tenancy_retention_days: int = Field(default=30, gt=0)
    socket_ticket_retention_hours: int = Field(default=24, gt=0)
    sign_in_delay_retention_hours: int = Field(default=720, gt=0)
    media_retention_days: int = Field(default=1, gt=0)
    media_pending_expiry_hours: int = Field(default=24, gt=0)
    # How many days a living org's events are kept; the sweep trims what is
    # older, a batch per org per call. 0 keeps every event and never moves a
    # floor (ADR 0040).
    event_retention_days: int = Field(default=90, ge=0)
