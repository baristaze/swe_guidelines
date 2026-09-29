"""Secrets: a capability, not a domain. The object model holds references;
the value is resolved at the point of use and discarded. Errors name the
secret and the store, never the value.

A secret belongs to a tenant, as a cache entry and a bucket object do: every
call takes the `org_id` first, and each tenant's secrets sit under a prefix
of their own, so a name one tenant presents never resolves to another
tenant's secret, or to one of the platform's. The platform's own credentials
(the database URL, the error tracker's DSN) are not read through here: they
reach the process at start."""

from abc import ABC, abstractmethod
from datetime import datetime
from uuid import UUID

from acme.infra.exceptions import InvalidSecretName, SecretNotFound, SecretsFileNotPrivate

__all__ = [
    "InvalidSecretName",
    "SecretNotFound",
    "SecretsFileNotPrivate",
    "SecretsInterface",
    "scoped_name",
]


def scoped_name(org_id: UUID, name: str) -> str:
    """The name a store keeps a tenant's secret under: `org/<org_id>/<name>`.
    A name is one segment, so no name climbs out of its tenant's prefix."""
    if not name or name != name.strip() or "/" in name or name in {".", ".."}:
        raise InvalidSecretName(f"a secret name is one segment with no '/': {name!r}")
    return f"org/{org_id}/{name}"


class SecretsInterface(ABC):
    """A call a request makes carries the request's deadline, and ends by
    then, unreachable (`acme.infra.deadline`); a worker's carries none."""

    @abstractmethod
    async def get(self, org_id: UUID, name: str, *, deadline: datetime | None = None) -> str:
        """Raises SecretNotFound."""
        ...

    @abstractmethod
    async def has(self, org_id: UUID, name: str) -> bool: ...

    @abstractmethod
    async def put(
        self, org_id: UUID, name: str, value: str, *, deadline: datetime | None = None
    ) -> None: ...

    @abstractmethod
    async def delete(
        self, org_id: UUID, name: str, *, deadline: datetime | None = None
    ) -> None: ...

    @abstractmethod
    def describe(self) -> str: ...

    @abstractmethod
    async def start(self) -> None:
        """Opened by the infra root at boot. An impl that holds no connection
        of its own returns None."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Closed by the infra root at shutdown, in reverse order of start."""
        ...
