"""Storage of the slack namespace: the org's installation, the install
states, and the record of what was posted. Every operation takes org_id
first, except the two lookups that find a tenant from what Slack sends (a
workspace, a state), which are cross-tenant by nature and read in the system
scope."""

from abc import ABC, abstractmethod
from datetime import datetime
from uuid import UUID

from acme.om.outbox.types.row import OutboxRow
from acme.om.slack.types.installation import SlackInstallation, SlackInstallState, SlackPost


class SlackStorageInterface(ABC):
    @abstractmethod
    async def read_installation(self, org_id: UUID) -> SlackInstallation | None:
        """The org's living installation; a deleted one reads as None."""
        ...

    @abstractmethod
    async def read_installation_by_team(
        self, team_id: str
    ) -> tuple[UUID, SlackInstallation] | None:
        """Cross-tenant, in the system scope: the living installation in a
        Slack workspace and the org it belongs to. A call from Slack arrives
        with a workspace and no tenant; this is how it finds one."""
        ...

    @abstractmethod
    async def write_installation(
        self,
        org_id: UUID,
        installation: SlackInstallation,
        outbox_rows: tuple[OutboxRow, ...],
    ) -> None:
        """Insert or update by id, with the rows that announce it in the same
        commit. A second living installation for the org, or a workspace
        another living installation holds, is `UniqueKeyTaken`, and nothing
        lands."""
        ...

    @abstractmethod
    async def claim_refresh(
        self, org_id: UUID, installation_id: UUID, now: datetime, until: datetime
    ) -> bool:
        """One conditional write: stamps `refreshing_until` on the living
        installation while no other renewal holds it at `now`. True when this
        caller holds the claim, so one renewal of the token runs at a time."""
        ...

    @abstractmethod
    async def settle_refresh(
        self,
        org_id: UUID,
        installation_id: UUID,
        token_expires_at: datetime | None,
        now: datetime,
    ) -> None:
        """Ends a renewal: when the new token expires, and the claim cleared.
        Nothing when the installation is gone meanwhile."""
        ...

    @abstractmethod
    async def create_install_state(self, org_id: UUID, state: SlackInstallState) -> None:
        """Lands a fresh state. Its digest is unique."""
        ...

    @abstractmethod
    async def redeem_install_state(
        self, state_hash: str, now: datetime
    ) -> tuple[UUID, SlackInstallState] | None:
        """Cross-tenant, in the system scope, one conditional write: stamps
        `redeemed_at` on the state with this digest while it is unredeemed and
        not expired at `now`, and returns it with its org. None when there is
        no such state, it was redeemed, or it expired: a state works once."""
        ...

    @abstractmethod
    async def read_post(self, org_id: UUID, key: UUID) -> SlackPost | None:
        """The message the work under this key posted, if it did."""
        ...

    @abstractmethod
    async def create_post(self, org_id: UUID, post: SlackPost) -> bool:
        """Records a posted message; False when its key is already recorded,
        which changes nothing. The key is unique per org."""
        ...

    @abstractmethod
    async def purge(self, before: datetime, limit: int) -> int:
        """Cross-tenant, for the sweep, in the system scope: the hard delete,
        one transaction a pass for every tenant: installations deleted,
        states expired or redeemed, and posts recorded before `before`, at
        most `limit` of each, whatever their tenant, skipping rows another
        transaction holds; returns how many rows went."""
        ...

    @abstractmethod
    async def purge_tenant(self, org_id: UUID, limit: int) -> int:
        """Every row of a deleted tenant once its retention has passed, at
        most `limit` of each kind per call."""
        ...
