"""The settings the integrations read, mixed into a process's one settings
object; nothing below reads the environment."""

from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from acme.infra.impl.settings import ENV_FILE


class IntegrationsSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ACME_", env_file=ENV_FILE, extra="ignore")

    # Which identity provider signs people in: WorkOS, its twin (local only,
    # refused at boot anywhere else), or none, which refuses every sign-in
    # through a provider and is what a process that signs nobody in holds.
    identity_provider: Literal["workos", "twin", "none"] = "none"
    # The Acme App application's client id. Not a secret: it is in every
    # authorization URL a browser sees. Each environment names its own
    # application in its deployment config.
    workos_client_id: str = ""
    # The Acme App application's API key, never the environment's: the
    # exchange's client secret and the management calls' key. A process
    # credential, injected at start from the secret store in a deployed
    # environment and read from the environment locally. Empty or "off"
    # means not set, and WorkOS is then not configured: the process starts,
    # says so, and every sign-in through it answers 503.
    workos_api_key: SecretStr | None = Field(default=None, repr=False)
    workos_base_url: str = "https://api.workos.com"
    workos_timeout_seconds: float = Field(default=10.0, gt=0)

    # The payment processor: Stripe, or its twin in memory (the tests, and
    # one process on a laptop), which is refused outside local and test.
    # Stripe with no key is unconfigured: every org keeps its plan.
    billing_backend: Literal["twin", "stripe"] = "stripe"
    # The account every call names in `Stripe-Context`. Not a secret: each
    # environment commits its own.
    stripe_account_id: str | None = None
    # The runtime key, and the signing secret of the endpoint the processor
    # delivers to. Both are process credentials, injected at start; empty or
    # "off" leaves billing unconfigured, which answers 503 and keeps every org
    # on its plan. The runtime key is a restricted key of the account that
    # may touch only what the API and the worker call (RUNTIME_PERMISSIONS).
    # The bootstrap's key is never a process's: `acme-ops stripe-bootstrap`
    # reads it from the person's shell.
    stripe_runtime_key: SecretStr | None = Field(default=None, repr=False)
    stripe_webhook_secret: SecretStr | None = Field(default=None, repr=False)
    stripe_timeout_seconds: float = Field(default=10.0, gt=0)

    # Slack: the app's real client, or its twin in memory (the tests, and a
    # laptop, where "Add to Slack" installs into the twin's own workspace),
    # which is refused outside local and test. The client id is not a secret:
    # every install page carries it, and each environment commits its own
    # app's. The client secret trades an install's code for its token and
    # renews tokens; the signing secret checks every call Slack makes in.
    # Both are process credentials, injected at start; empty or "off" leaves
    # Slack unconfigured, which answers 503 and posts nothing.
    slack_backend: Literal["twin", "slack"] = "slack"
    slack_client_id: str = ""
    slack_client_secret: SecretStr | None = Field(default=None, repr=False)
    slack_signing_secret: SecretStr | None = Field(default=None, repr=False)
    slack_timeout_seconds: float = Field(default=10.0, gt=0)

    @field_validator("workos_api_key")
    @classmethod
    def _key_off_is_none(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None or value.get_secret_value().strip().lower() in ("", "off"):
            return None
        return value

    @field_validator(
        "stripe_runtime_key",
        "stripe_webhook_secret",
        "slack_client_secret",
        "slack_signing_secret",
        mode="before",
    )
    @classmethod
    def _secret_off_is_none(cls, value: object) -> object:
        """The cloud secret starts as "off", as the error tracker's DSN does."""
        if isinstance(value, str) and value.strip().lower() in ("", "off"):
            return None
        return value

    @field_validator("stripe_account_id", mode="before")
    @classmethod
    def _empty_account_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value


LIVE_PREFIXES = ("sk_live_", "rk_live_", "sk_org_live_", "rk_org_live_")
TEST_PREFIXES = ("sk_test_", "rk_test_", "sk_org_test_", "rk_org_test_")


def key_mode(key: str) -> Literal["live", "test"] | None:
    """What a processor key's prefix says it moves: real money or none."""
    if key.startswith(LIVE_PREFIXES):
        return "live"
    if key.startswith(TEST_PREFIXES):
        return "test"
    return None


def mode_for(environment: str) -> Literal["live", "test"]:
    """Production takes a live key and every other environment a test key, so
    a laptop or staging never moves real money and production never answers
    with a sandbox."""
    return "live" if environment == "production" else "test"


ORGANIZATION_PREFIXES = ("sk_org_", "rk_org_")


def key_kind(key: str) -> Literal["restricted", "secret", "organization"] | None:
    """What a processor key's prefix says it may reach: an organization key
    every account of the organization, a secret key everything in one
    account, a restricted key only what it was given in one account."""
    if key.startswith(ORGANIZATION_PREFIXES):
        return "organization"
    if key.startswith("rk_"):
        return "restricted"
    if key.startswith("sk_"):
        return "secret"
    return None


def key_refusal(variable: str, key: str, environment: str) -> str | None:
    """Why a key is refused for the environment, or None when it is taken.
    Only a restricted key is taken, and only in the environment's mode. The
    message names the variable and never the key."""
    kind = key_kind(key)
    if kind == "organization":
        return (
            f"{variable} is an organization key, which reaches every account of the "
            "organization; make a restricted key in the environment's account "
            "(docs/runbooks/providers/stripe.md)"
        )
    if kind == "secret":
        return (
            f"{variable} is a secret key, which may do everything in the account; make a "
            "restricted key with the permissions docs/runbooks/providers/stripe.md lists"
        )
    expected = mode_for(environment)
    if kind is None:
        return f"{variable} is not a restricted key; {environment} takes rk_{expected}_"
    found = key_mode(key) or "unrecognised"
    if found != expected:
        return f"{variable} is a {found} key; {environment} takes a {expected} key"
    return None
