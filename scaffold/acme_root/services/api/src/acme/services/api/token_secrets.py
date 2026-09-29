"""Where the grant job hands over an operator token it minted: the secret
store of the environment, under a name of its own per identity, written
with `PutSecretValue` alone. The secret exists already, empty, declared by
the environment's infrastructure; the grant task may write a new version of
it and nothing else, so this never creates one, never reads one, and never
goes through the tenant-scoped secrets capability, whose names carry a
tenant's prefix."""

from datetime import timedelta
from typing import Any

import aioboto3

from acme.infra.aws_clients import client_config
from acme.services.api.settings import ApiSettings

TOKEN_HOLDERS = ("provisioner", "smoke")
"""The identities no person signs in as, whose tokens the grant job mints."""


def token_secret_name(environment: str, holder: str) -> str:
    """`acme-<env>-provisioner-token` and `acme-<env>-smoke-token`."""
    if holder not in TOKEN_HOLDERS:
        raise ValueError(f"no operator token is minted for {holder!r}")
    return f"acme-{environment}-{holder}-token"


async def put_token(settings: ApiSettings, name: str, token: str) -> None:
    """A new version of the named secret, holding the token and nothing else."""
    config = client_config(timedelta(seconds=settings.aws_timeout_seconds))
    # aioboto3's clients are typed loosely; the infra roots hold theirs as Any too.
    opened: Any = aioboto3.Session().client(
        "secretsmanager", region_name=settings.aws_region, config=config
    )
    async with opened as client:
        await client.put_secret_value(SecretId=name, SecretString=token)
