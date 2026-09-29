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

    # The signing secret of the endpoint the provider delivers its events
    # to (`/webhooks/identity`). A process credential, injected at start;
    # empty or "off" leaves the route refusing every delivery. The twin signs
    # with its own and ignores this one.
    workos_webhook_secret: SecretStr | None = Field(default=None, repr=False)

    @field_validator("workos_api_key")
    @classmethod
    def _key_off_is_none(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None or value.get_secret_value().strip().lower() in ("", "off"):
            return None
        return value

    @field_validator("workos_webhook_secret", mode="before")
    @classmethod
    def _secret_off_is_none(cls, value: object) -> object:
        """The cloud secret starts as "off", as the error tracker's DSN does."""
        if isinstance(value, str) and value.strip().lower() in ("", "off"):
            return None
        return value
