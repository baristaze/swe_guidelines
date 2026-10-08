"""Every knob of the API process is documented and wired: each field of
`ApiSettings` appears in `.env.example` under its prefix, and each field a
deployed process cannot leave at its local default is set in
`app_environment` or a secret of every Terraform environment. Both files are
read as text, the way a reviewer reads them. A field the cloud leaves at its
default is listed here with the reason, so a new field needs a decision."""

import re
from datetime import timedelta
from pathlib import Path

import pytest

from acme.om.tenancy.impl.manager import TenancyOptions
from acme.services.api.settings import ApiSettings

PREFIX = ApiSettings.model_config.get("env_prefix", "")

NOT_A_KNOB = {
    "secret_overrides": "the ACME_SECRET_<ORG>_<NAME> family, documented by its example line",
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
    "namespaces": "one process serves every namespace until a split names a subset",
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
    "version": "the image carries it",
    "login_rate_limit": "the local budget is the budget",
    "login_rate_window_seconds": "the local window is the window",
    "credential_rate_limit_reads": "the local budget is the budget",
    "credential_rate_limit_writes": "the local budget is the budget",
    "credential_rate_window_seconds": "the local window is the window",
    "failed_authentication_limit": "the local budget is the budget",
    "failed_authentication_window_seconds": "the local window is the window",
    "sign_in_free_failures": "the local run is the run",
    "sign_in_delay_base_seconds": "the local delay is the delay",
    "sign_in_delay_cap_seconds": "the local cap is the cap",
    "login_lifetime_seconds": "the local lifetime is the lifetime",
    "session_lifetime_seconds": "the local lifetime is the lifetime",
    "session_idle_lifetime_seconds": "the local lifetime is the lifetime",
    "operator_token_max_lifetime_seconds": "an hour, the guideline's bound, everywhere",
    "dev_sign_in_enabled": "off everywhere it is not set, and refused outside local and test",
    "invitation_lifetime_days": "the local lifetime is the lifetime",
    "workos_base_url": "the hosted API everywhere",
    "workos_timeout_seconds": "the local default is the tuning",
    "realtime_send_buffer_size": "the local size is the size",
    "realtime_control_buffer_size": "the local size is the size",
    "realtime_recheck_seconds": "the local interval is the interval",
    "realtime_head_max_age_seconds": "the local bound is the bound",
    "readiness_timeout_seconds": "shorter than every probe interval the deployment sets",
    "admission_limit_reads": "the local bound is the bound until a replica is measured",
    "admission_limit_writes": "the local bound is the bound until a replica is measured",
    "admission_retry_after_seconds": "the local wait is the wait",
    "request_deadline_seconds": "the local deadline is the deadline, under the clients' 30 s",
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
        f"{PREFIX}{field.upper()}" for field in ApiSettings.model_fields if field not in NOT_A_KNOB
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
        for field in ApiSettings.model_fields
        if field not in NOT_A_KNOB
        and field not in LOCAL_DEFAULT_SERVES_THE_CLOUD
        and f"{PREFIX}{field.upper()}" not in wired
    )
    assert not unwired, f"{environment.name} sets no value for: {unwired}"
    fields = ApiSettings.model_fields
    stale = sorted(field for field in LOCAL_DEFAULT_SERVES_THE_CLOUD if field not in fields)
    assert not stale, f"exceptions naming no field: {stale}"


def module_blocks(terraform: str) -> dict[str, str]:
    """Each `module "<name>" { ... }` block of a Terraform file, by name, read
    by matching braces."""
    blocks: dict[str, str] = {}
    for match in re.finditer(r'^module "([a-z_]+)" \{', terraform, flags=re.MULTILINE):
        depth, end = 0, match.end() - 1
        for end in range(match.end() - 1, len(terraform)):
            depth += {"{": 1, "}": -1}.get(terraform[end], 0)
            if depth == 0:
                break
        blocks[match.group(1)] = terraform[match.start() : end + 1]
    return blocks


def test_every_task_of_the_api_image_boots_the_api_sign_in_settings() -> None:
    """The service and the one-off tasks that run the API's image (migrate,
    grant) all boot `ApiSettings`, and a deployed environment refuses its
    sign-in settings left at their local defaults. So each of them is given
    the one shared set, not the service alone: a task without it fails at
    boot, and a migrate task that fails stops the release."""
    environment = repository_root() / "deployment" / "terraform" / "modules" / "environment"
    blocks = module_blocks((environment / "main.tf").read_text())
    api_image = {name: block for name, block in blocks.items() if "var.api_image" in block}
    assert {"api", "migrate", "grant"} <= set(api_image)
    missing = sorted(
        name for name, block in api_image.items() if "local.api_sign_in_environment" not in block
    )
    assert not missing, f"tasks of the API image without the sign-in settings: {missing}"


def test_a_session_lasts_thirty_days_and_fourteen_idle() -> None:
    """The session lifetimes are the same everywhere they are written: the
    setting's default, `.env.example`, and the tenancy manager's own default
    (ADR 0063)."""
    absolute, idle = timedelta(days=30), timedelta(days=14)
    fields = ApiSettings.model_fields
    assert timedelta(seconds=fields["session_lifetime_seconds"].default) == absolute
    assert timedelta(seconds=fields["session_idle_lifetime_seconds"].default) == idle
    documented = dict(
        re.findall(
            r"^([A-Z][A-Z0-9_]*)=(.*)$",
            (repository_root() / ".env.example").read_text(),
            flags=re.MULTILINE,
        )
    )
    assert timedelta(seconds=int(documented[f"{PREFIX}SESSION_LIFETIME_SECONDS"])) == absolute
    assert timedelta(seconds=int(documented[f"{PREFIX}SESSION_IDLE_LIFETIME_SECONDS"])) == idle
    options = TenancyOptions()
    assert (options.session_ttl, options.session_idle_ttl) == (absolute, idle)
