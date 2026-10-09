"""Every knob of the maintenance worker is documented and wired: each field of
`MaintenanceSettings` appears in `.env.example` under its prefix, and each field a
deployed process cannot leave at its local default is set in
`app_environment` or a secret of every Terraform environment. Both files are
read as text, the way a reviewer reads them. A field the cloud leaves at its
default is listed here with the reason, so a new field needs a decision."""

import re
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from acme.infra.impl.local import InfraLocalImpl
from acme.om.storage.impl.memory import StorageMemoryImpl
from acme.workers.maintenance.container import (
    MEDIA_PURGE_BATCH,
    WorkerContainer,
    events_options,
)
from acme.workers.maintenance.main import loop_options
from acme.workers.maintenance.settings import MaintenanceSettings

PREFIX = MaintenanceSettings.model_config.get("env_prefix", "")

NOT_A_KNOB = {
    "secret_overrides": "the ACME_SECRET_<NAME> family, documented by its example line",
}

LOCAL_DEFAULT_SERVES_THE_CLOUD = {
    "database_url_core": "one URL for every role until a role moves out",
    "database_url_activity": "one URL for every role until a role moves out",
    "database_url_queue": "one URL for every role until a role moves out",
    "database_url_admin": "one URL for every role until a role moves out",
    "database_pool_size": "the local default is the tuning",
    "database_pool_size_core": "one pool for every role until a role moves out",
    "database_pool_size_activity": "one pool for every role until a role moves out",
    "database_pool_size_queue": "one pool for every role until a role moves out",
    "database_pool_size_admin": "one pool for every role until a role moves out",
    "database_checkout_timeout_seconds": "the local default is the tuning",
    "database_checkout_timeout_seconds_core": "one pool for every role until a role moves out",
    "database_checkout_timeout_seconds_activity": "one pool for every role until a role moves out",
    "database_checkout_timeout_seconds_queue": "one pool for every role until a role moves out",
    "database_checkout_timeout_seconds_admin": "one pool for every role until a role moves out",
    "database_statement_timeout_seconds": "the local default is the tuning",
    "database_statement_timeout_seconds_core": "one pool for every role until a role moves out",
    "database_statement_timeout_seconds_activity": "one pool for every role until a role moves out",
    "database_statement_timeout_seconds_queue": "one pool for every role until a role moves out",
    "database_statement_timeout_seconds_admin": "one pool for every role until a role moves out",
    "buckets_root": "the local buckets backend only",
    "s3_endpoint_url": "the hosted endpoint; only MinIO needs one",
    "s3_presign_endpoint_url": "the hosted endpoint is the browser's too; only MinIO needs one",
    "s3_access_key": "the task role signs; only MinIO needs a key",
    "s3_secret_key": "the task role signs; only MinIO needs a key",
    "sqs_endpoint_url": "the hosted endpoint; only ElasticMQ needs one",
    "secrets_file": "the local secrets backend only",
    "flags_file": "the local flags backend only",
    "log_level": "INFO everywhere",
    "otel_endpoint": "traces go to the collector sidecar, wired in the task, not a knob",
    "aws_timeout_seconds": "the local default is the tuning",
    "valkey_timeout_seconds": "the local default is the tuning",
    "valkey_breaker_failures": "the local bound is the bound",
    "valkey_breaker_cooldown_seconds": "the local cool-down is the cool-down",
    "otel_timeout_seconds": "the local default is the tuning",
    "flags_timeout_seconds": "the local default is the tuning",
    "workos_base_url": "the worker signs nobody in",
    "workos_timeout_seconds": "the worker signs nobody in",
    "version": "the image carries it",
    "metrics_host": "the collector sidecar shares the task's network namespace; 127.0.0.1 serves",
    "metrics_port": "9464, the port the service module tells the sidecar to scrape",
    "worker_id": "maintenance-<hostname>-<pid>, the key the loop heartbeats under",
    "worker_lane": "the default lane is the one lane",
    "worker_tenant_cap": "no cap until a lane's tenants crowd each other",
    "worker_capacity": "the local default is the tuning",
    "worker_lease_seconds": "the local default is the tuning",
    "worker_heartbeat_seconds": "the local default is the tuning",
    "worker_sweep_seconds": "the local default is the tuning",
    "worker_poll_seconds": "the local default is the tuning",
    "worker_purge_batch": "the local default is the tuning",
    "worker_sweep_budget_seconds": "the local default is the tuning",
    "worker_tally_seconds": "the local default is the tuning",
    "outbox_retention_days": "one retention everywhere; it outlives the backups",
    "work_retention_days": "one retention everywhere",
    "idempotency_retention_hours": "one retention everywhere",
    "tenancy_retention_days": "one retention everywhere",
    "socket_ticket_retention_hours": "one retention everywhere",
    "sign_in_delay_retention_hours": "one retention everywhere",
    "media_retention_days": "one retention everywhere",
    "media_pending_expiry_hours": "one retention everywhere",
    "event_retention_days": "one retention everywhere, set in code (ADR 0040)",
}


def repository_root() -> Path:
    """The checkout this test runs in: the nearest folder above it with an
    `.env.example`, so a copy that is not yet a repository reads its own."""
    here = Path(__file__).resolve().parent
    return next(p for p in (here, *here.parents) if (p / ".env.example").is_file())


def documented_knobs(env_example: str) -> set[str]:
    """The names on `NAME=` and `#NAME=` lines."""
    return set(re.findall(r"^#?([A-Z][A-Z0-9_]*)=", env_example, flags=re.MULTILINE))


def wired_knobs(terraform: str) -> set[str]:
    """The names given a value anywhere in the environment's graph: the
    shared `app_environment`, the process secrets, and a service's own block."""
    return set(re.findall(r"\b(ACME_[A-Z0-9_]+)\s*=", terraform))


def graph(environment: Path) -> str:
    """The root and every module it calls: an environment root is thin, and
    the graph it applies lives in the module it names."""
    root = (environment / "main.tf").read_text()
    sources = re.findall(r'source\s*=\s*"([^"]+)"', root)
    return "\n".join(
        [root] + [(environment / source / "main.tf").read_text() for source in sources]
    )


def knobs() -> list[str]:
    return [
        f"{PREFIX}{field.upper()}"
        for field in MaintenanceSettings.model_fields
        if field not in NOT_A_KNOB
    ]


def test_every_setting_is_in_env_example() -> None:
    documented = documented_knobs((repository_root() / ".env.example").read_text())
    missing = sorted(knob for knob in knobs() if knob not in documented)
    assert not missing, f"settings fields absent from .env.example: {missing}"


def environments() -> list[Path]:
    return sorted((repository_root() / "deployment" / "terraform" / "environments").iterdir())


@pytest.mark.parametrize("environment", environments(), ids=lambda path: path.name)
def test_every_setting_the_cloud_needs_is_wired(environment: Path) -> None:
    wired = wired_knobs(graph(environment))
    unwired = sorted(
        f"{PREFIX}{field.upper()}"
        for field in MaintenanceSettings.model_fields
        if field not in NOT_A_KNOB
        and field not in LOCAL_DEFAULT_SERVES_THE_CLOUD
        and f"{PREFIX}{field.upper()}" not in wired
    )
    assert not unwired, f"{environment.name} sets no value for: {unwired}"
    stale = sorted(
        field
        for field in LOCAL_DEFAULT_SERVES_THE_CLOUD
        if field not in MaintenanceSettings.model_fields
    )
    assert not stale, f"exceptions naming no field: {stale}"


BOUNDED = (
    "worker_capacity",
    "worker_lease_seconds",
    "worker_heartbeat_seconds",
    "worker_sweep_seconds",
    "worker_poll_seconds",
    "worker_purge_batch",
    "worker_sweep_budget_seconds",
    "outbox_retention_days",
    "work_retention_days",
    "idempotency_retention_hours",
    "tenancy_retention_days",
    "socket_ticket_retention_hours",
    "sign_in_delay_retention_hours",
    "media_retention_days",
    "media_pending_expiry_hours",
)


@pytest.mark.parametrize("field", BOUNDED)
def test_a_count_or_a_duration_of_zero_is_refused(field: str) -> None:
    """Zero is not a smaller setting here but a broken one: a lease of zero
    cancels every item as `lease_lost` the moment it is claimed, a heartbeat
    of zero never lets the claim loop run, and a capacity of zero claims
    nothing. The process refuses to start rather than run that way."""
    base = {"database_url_core": "postgresql+asyncpg://t/t"}
    with pytest.raises(ValidationError):
        MaintenanceSettings.model_validate({**base, field: 0})
    with pytest.raises(ValidationError):
        MaintenanceSettings.model_validate({**base, field: -1})


def test_the_sweep_defaults_keep_what_each_namespace_kept() -> None:
    """Each retention defaults to what its manager kept before it was a
    setting, but for the socket tickets, which live a minute and are kept a
    day. The worker hands each one to its manager."""
    settings = MaintenanceSettings.model_validate({"_env_file": None, "environment": "test"})
    assert (settings.worker_purge_batch, settings.worker_sweep_budget_seconds) == (1000, 20)
    assert settings.outbox_retention_days == 8
    assert settings.work_retention_days == 30
    assert settings.idempotency_retention_hours == 24
    assert settings.tenancy_retention_days == 30
    assert settings.socket_ticket_retention_hours == 24
    assert settings.sign_in_delay_retention_hours == 30 * 24
    assert (settings.media_retention_days, settings.media_pending_expiry_hours) == (1, 24)


def test_the_worker_hands_each_retention_to_its_manager(tmp_path: Path) -> None:
    settings = MaintenanceSettings.model_validate(
        {
            "_env_file": None,
            "environment": "test",
            "worker_id": "maintenance-test",
            "worker_purge_batch": 7,
            "media_retention_days": 3,
            "socket_ticket_retention_hours": 2,
        }
    )
    container = WorkerContainer.for_tests(
        StorageMemoryImpl(), InfraLocalImpl(tmp_path), settings=settings
    )
    media = container.managers.media._options  # type: ignore[attr-defined]
    tenancy = container.managers.tenancy._options  # type: ignore[attr-defined]
    assert (media.retention, media.purge_batch) == (timedelta(days=3), MEDIA_PURGE_BATCH)
    assert (tenancy.ticket_retention, tenancy.purge_batch) == (timedelta(hours=2), 7)
    assert tenancy.retention == timedelta(days=30)
    options = loop_options(settings)
    assert (options.purge_batch, options.sweep_budget) == (7, timedelta(seconds=20))
    assert options.outbox_retention == timedelta(days=8)
    assert options.tally_interval == timedelta(minutes=5)


def test_the_tenant_cap_is_the_lanes_and_0_sets_none() -> None:
    """The cap travels with the lane the worker claims from. 0 sets no lane
    cap, so the claim counts only the tenants with a cap of their own."""
    base = {"_env_file": None, "environment": "test", "worker_id": "maintenance-test"}
    assert loop_options(MaintenanceSettings.model_validate(base)).tenant_cap is None
    capped = MaintenanceSettings.model_validate({**base, "worker_tenant_cap": 2})
    assert (loop_options(capped).tenant_cap, loop_options(capped, "bulk").tenant_cap) == (2, 2)
    with pytest.raises(ValidationError):
        MaintenanceSettings.model_validate({**base, "worker_tenant_cap": -1})


def test_the_event_retention_keeps_ninety_days_and_0_turns_it_off() -> None:
    """The sweep trims events older than 90 days by default. 0 keeps every
    event: the sweep never moves a floor (ADR 0040)."""
    base = {"_env_file": None, "environment": "test"}
    assert events_options(MaintenanceSettings.model_validate(base)).retention == timedelta(days=90)
    off = MaintenanceSettings.model_validate({**base, "event_retention_days": 0})
    assert events_options(off).retention is None
    with pytest.raises(ValidationError):
        MaintenanceSettings.model_validate({**base, "event_retention_days": -1})
