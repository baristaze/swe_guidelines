"""The infra half of every process's settings object. Backends are selected
here and nowhere else, and this is the only module below the container
that reads the environment."""

import os
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ENVIRONMENTS = frozenset({"local", "test", "dev", "staging", "production"})
"""The one set of environment names, shared with deployment/terraform."""

CLOUD_ENVIRONMENTS = frozenset({"dev", "staging", "production"})
"""The deployed environments: each refuses every local-only backend at boot."""

SECRET_ENV_PREFIX = "ACME_SECRET_"

ENV_FILE = ".env"
"""The dotenv file every settings object in the repository reads."""


def secret_overrides_from_environment() -> dict[str, str]:
    """ACME_SECRET_<NAME>=value, collected once at boot and handed to the local
    secrets impl, keyed by NAME.

    Both sources every other setting has, in the same order: the dotenv file
    first, the process environment over it. Pydantic's own dotenv source
    cannot supply these, since it matches declared fields and one key per
    secret is not a field; without the file half, a knob `.env.example`
    documents as a `.env` knob worked only through `scripts/dev.sh` and
    compose (which export), and `make seed` or a process run by hand got
    `SecretNotFound` with nothing to go on."""
    from_file = {key: value for key, value in dotenv_values(ENV_FILE).items() if value is not None}
    return {
        key[len(SECRET_ENV_PREFIX) :]: value
        for key, value in {**from_file, **os.environ}.items()
        if key.startswith(SECRET_ENV_PREFIX) and len(key) > len(SECRET_ENV_PREFIX)
    }


class InfraSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ACME_", env_file=ENV_FILE, extra="ignore")

    environment: str = "local"

    cache_backend: Literal["memory", "valkey"] = "memory"
    topics_backend: Literal["memory", "valkey"] = "memory"
    valkey_url: str = "valkey://127.0.0.1:56379/0"

    # The one breaker in front of Valkey, shared by every cache scope and the
    # topic publisher. A Valkey that is down answers every call with the whole
    # of `valkey_timeout_seconds`, and the timeouts alone are what exhaust the
    # pool the calls are made from. After this many calls in a row that spend
    # the timeout, each answers at once for the cool-down the way that backend
    # failing answers, then one call goes through to decide whether to close.
    # Opening costs failures * timeout, and the cool-down is what that buys, so
    # the cool-down is worth several times the timeout.
    valkey_breaker_failures: int = Field(default=3, ge=1)
    valkey_breaker_cooldown_seconds: float = Field(default=30.0, gt=0)

    buckets_backend: Literal["local", "s3"] = "local"
    buckets_root: Path = Path(".local/buckets")
    s3_endpoint_url: str | None = None
    # The address a browser reaches the store at, when it is not the one this
    # process uses: a presigned URL names it. Empty, the two are one.
    s3_presign_endpoint_url: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_bucket_prefix: str = "acme-local"

    queues_backend: Literal["memory", "sqs"] = "memory"
    sqs_endpoint_url: str | None = None
    sqs_queue_prefix: str = "acme-"

    secrets_backend: Literal["local", "aws"] = "local"
    secrets_file: Path | None = Path(".local/secrets.env")
    secrets_name_prefix: str = "acme/"
    secret_overrides: dict[str, str] = Field(
        default_factory=secret_overrides_from_environment, exclude=True, repr=False
    )

    aws_region: str = "us-east-1"

    # Every outbound call carries a timeout, one per client, so a downstream
    # that hangs cannot hold a replica's whole pool: the AWS clients (connect
    # and read), the Valkey client (per request), the trace exporter (per batch).
    aws_timeout_seconds: float = 10.0
    valkey_timeout_seconds: float = 5.0
    otel_timeout_seconds: float = 10.0

    log_level: str = "INFO"
    log_json: bool = False
    otel_endpoint: str | None = None
    sentry_dsn: str | None = None

    @field_validator("s3_presign_endpoint_url")
    @classmethod
    def _presign_endpoint_empty_is_none(cls, value: str | None) -> str | None:
        """Empty, as `.env.example` leaves it, means the endpoint itself."""
        return value or None

    @field_validator("sentry_dsn")
    @classmethod
    def _dsn_off_is_none(cls, value: str | None) -> str | None:
        """Empty or "off" means no reporting; the cloud secret starts as "off"."""
        if value is None or value.strip().lower() in ("", "off"):
            return None
        return value

    @property
    def is_known_environment(self) -> bool:
        return self.environment in ENVIRONMENTS

    @property
    def is_cloud_environment(self) -> bool:
        return self.environment in CLOUD_ENVIRONMENTS
