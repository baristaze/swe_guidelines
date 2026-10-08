"""Feature flags: a capability, as the cache and the secrets are. A flag is
a release toggle or a kill switch, never what a tenant may do: that is a
modelled entity with a manager.

Every flag is declared here, in code, with its default and whether a client
may read it. A provider only overrides a declared flag's value for an
audience, and a flag it does not know, or a provider that fails, reads the
default declared here.

The audience is an org first and, optionally, a person inside it: the
`user_id` of the identity that acts in that org. One call evaluates every
declared flag for one audience, and the precedence is the same in every
impl: a rule on the user, then a rule on the org, then the provider's own
default for the flag, then the code's."""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from uuid import UUID

from acme.infra.base import InfraModel


class Flag(StrEnum):
    MEDIA_UPLOADS = "media-uploads"


class FlagSpec(InfraModel):
    default: bool
    client: bool
    """Whether a client may read it: only such a flag reaches a client's
    snapshot. A flag a client must not see is read on the server alone."""


FLAGS: Mapping[Flag, FlagSpec] = MappingProxyType(
    {
        # A new upload is refused while it is off: the switch that stops
        # uploads for an org, or for everyone, without a deploy.
        Flag.MEDIA_UPLOADS: FlagSpec(default=True, client=True),
    }
)
"""Every declared flag, with its default and its mark."""


class FlagSet(InfraModel):
    """Every declared flag's value for one audience."""

    values: dict[Flag, bool]

    def on(self, flag: Flag) -> bool:
        return self.values[flag]

    def for_clients(self) -> dict[Flag, bool]:
        """The values of the flags marked for clients, and no other."""
        return {flag: value for flag, value in self.values.items() if FLAGS[flag].client}


def code_defaults() -> FlagSet:
    """What every flag reads with no provider, or with one that fails."""
    return FlagSet(values={flag: spec.default for flag, spec in FLAGS.items()})


class FlagsInterface(ABC):
    @abstractmethod
    async def evaluate(self, org_id: UUID, user_id: UUID | None = None) -> FlagSet:
        """Every declared flag for the org, and for the person in it when
        `user_id` is given. Never raises: a provider that fails reads the
        code's defaults."""
        ...

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
