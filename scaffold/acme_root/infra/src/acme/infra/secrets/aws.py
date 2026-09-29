from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import aioboto3

from acme.infra.aws_clients import AwsClientHolder, client_config
from acme.infra.aws_errors import ClientError, error_code, translated
from acme.infra.deadline import bounded, unreachable
from acme.infra.secrets import SecretNotFound, SecretsInterface, scoped_name


class SecretsAwsImpl(SecretsInterface):
    """One client, opened at start() and closed at close(). The deployed task
    roles read the application prefix and nothing more (modules/secrets and
    the task boundary), so `put` and `delete` are refused in the cloud until
    the change that first needs them widens both."""

    def __init__(
        self, session: aioboto3.Session, *, region: str, name_prefix: str, timeout: timedelta
    ) -> None:
        self._name_prefix = name_prefix
        self._region = region
        config = client_config(timeout)
        self._holder = AwsClientHolder(
            "secretsmanager",
            lambda: session.client("secretsmanager", region_name=region, config=config),
        )

    def _client(self) -> Any:
        return self._holder.client()

    def _name(self, org_id: UUID, name: str) -> str:
        return f"{self._name_prefix}{scoped_name(org_id, name)}"

    async def get(self, org_id: UUID, name: str, *, deadline: datetime | None = None) -> str:
        async with bounded(deadline, unreachable("secretsmanager", "get")):
            with translated("secretsmanager", "get"):
                try:
                    response = await self._client().get_secret_value(
                        SecretId=self._name(org_id, name)
                    )
                except ClientError as error:
                    if error_code(error) == "ResourceNotFoundException":
                        raise SecretNotFound(name, "aws secrets manager") from None
                    raise
        return response["SecretString"]

    async def has(self, org_id: UUID, name: str) -> bool:
        """Existence is metadata: the value never leaves the store for a
        question that does not need it."""
        with translated("secretsmanager", "has"):
            try:
                await self._client().describe_secret(SecretId=self._name(org_id, name))
            except ClientError as error:
                if error_code(error) == "ResourceNotFoundException":
                    return False
                raise
        return True

    async def put(
        self, org_id: UUID, name: str, value: str, *, deadline: datetime | None = None
    ) -> None:
        """Create, and on the store saying it exists, write a new version:
        one call in the common case and no window between a read and a
        write for another writer to slip into. The deadline bounds both."""
        async with bounded(deadline, unreachable("secretsmanager", "put")):
            with translated("secretsmanager", "put"):
                client = self._client()
                try:
                    await client.create_secret(Name=self._name(org_id, name), SecretString=value)
                except ClientError as error:
                    if error_code(error) != "ResourceExistsException":
                        raise
                    await client.put_secret_value(
                        SecretId=self._name(org_id, name), SecretString=value
                    )

    async def delete(self, org_id: UUID, name: str, *, deadline: datetime | None = None) -> None:
        """Idempotent, as the local twin is: a secret that is not there is
        already deleted."""
        async with bounded(deadline, unreachable("secretsmanager", "delete")):
            with translated("secretsmanager", "delete"):
                try:
                    await self._client().delete_secret(
                        SecretId=self._name(org_id, name), ForceDeleteWithoutRecovery=True
                    )
                except ClientError as error:
                    if error_code(error) != "ResourceNotFoundException":
                        raise

    def describe(self) -> str:
        return f"secrets=aws({self._region})"

    async def start(self) -> None:
        await self._holder.open()

    async def close(self) -> None:
        await self._holder.close()
