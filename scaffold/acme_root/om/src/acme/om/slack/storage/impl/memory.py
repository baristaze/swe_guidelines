from datetime import datetime
from uuid import UUID

from acme.om.exceptions import UniqueKeyTaken
from acme.om.outbox.storage import OutboxLandingInterface
from acme.om.outbox.types.row import OutboxRow
from acme.om.slack.storage import SlackStorageInterface
from acme.om.slack.types.installation import SlackInstallation, SlackInstallState, SlackPost
from acme.om.storage.impl.memory_base import MemoryStorageBase, MemoryTable


class SlackStorageMemoryImpl(MemoryStorageBase, SlackStorageInterface):
    def __init__(self, outbox: OutboxLandingInterface | None = None) -> None:
        super().__init__(outbox)
        self._installations: MemoryTable[SlackInstallation] = {}
        self._states: MemoryTable[SlackInstallState] = {}
        self._posts: MemoryTable[SlackPost] = {}

    async def read_installation(self, org_id: UUID) -> SlackInstallation | None:
        living = [i for i in self._rows(self._installations, org_id) if i.deleted_at is None]
        return living[0] if living else None

    async def read_installation_by_team(
        self, team_id: str
    ) -> tuple[UUID, SlackInstallation] | None:
        for org_id, installation in self._rows_across_tenants(self._installations):
            if installation.deleted_at is None and installation.team_id == team_id:
                return org_id, installation
        return None

    async def write_installation(
        self,
        org_id: UUID,
        installation: SlackInstallation,
        outbox_rows: tuple[OutboxRow, ...],
    ) -> None:
        async with self._lock:
            self._fence(self._installations, org_id, installation)
            if installation.deleted_at is None:
                # The two partial unique indexes: among the living, one per org
                # and one org per workspace.
                for row_org, other in self._installations.values():
                    if other.id == installation.id or other.deleted_at is not None:
                        continue
                    if row_org == org_id:
                        raise UniqueKeyTaken("uq_slack_installations_org_id is taken")
                    if other.team_id == installation.team_id:
                        raise UniqueKeyTaken("uq_slack_installations_team_id is taken")
            self._put(self._installations, org_id, installation, outbox_rows)

    async def claim_refresh(
        self, org_id: UUID, installation_id: UUID, now: datetime, until: datetime
    ) -> bool:
        async with self._lock:
            found = self._get(self._installations, org_id, installation_id)
            if found is None or found.deleted_at is not None:
                return False
            if found.refreshing_until is not None and found.refreshing_until > now:
                return False
            self._installations[found.id] = (
                org_id,
                found.model_copy(update={"refreshing_until": until}),
            )
            return True

    async def settle_refresh(
        self,
        org_id: UUID,
        installation_id: UUID,
        token_expires_at: datetime | None,
        now: datetime,
    ) -> None:
        async with self._lock:
            found = self._get(self._installations, org_id, installation_id)
            if found is None or found.deleted_at is not None:
                return
            self._installations[found.id] = (
                org_id,
                found.model_copy(
                    update={"token_expires_at": token_expires_at, "refreshing_until": None}
                ),
            )

    async def create_install_state(self, org_id: UUID, state: SlackInstallState) -> None:
        async with self._lock:
            if any(other.state_hash == state.state_hash for other in self._every(self._states)):
                raise UniqueKeyTaken("uq_slack_install_states_state_hash is taken")
            if not self._insert(self._states, org_id, state):
                raise UniqueKeyTaken("pk_slack_install_states is taken")

    async def redeem_install_state(
        self, state_hash: str, now: datetime
    ) -> tuple[UUID, SlackInstallState] | None:
        async with self._lock:
            for org_id, state in self._rows_across_tenants(self._states):
                if state.state_hash != state_hash:
                    continue
                if state.redeemed_at is not None or state.expires_at <= now:
                    return None
                redeemed = state.model_copy(update={"redeemed_at": now})
                self._states[state.id] = (org_id, redeemed)
                return org_id, redeemed
            return None

    async def read_post(self, org_id: UUID, key: UUID) -> SlackPost | None:
        return next((p for p in self._rows(self._posts, org_id) if p.key == key), None)

    async def create_post(self, org_id: UUID, post: SlackPost) -> bool:
        async with self._lock:
            if any(p.key == post.key for p in self._rows(self._posts, org_id)):
                return False
            if not self._insert(self._posts, org_id, post):
                raise UniqueKeyTaken("pk_slack_posts is taken")
            return True

    async def purge(self, before: datetime, limit: int) -> int:
        async with self._lock:
            gone = [
                i.id
                for _, i in self._rows_across_tenants(self._installations)
                if i.deleted_at is not None and i.deleted_at < before
            ][:limit]
            for key in gone:
                del self._installations[key]
            states = [
                s.id
                for _, s in self._rows_across_tenants(self._states)
                if s.expires_at < before or (s.redeemed_at is not None and s.redeemed_at < before)
            ][:limit]
            for key in states:
                del self._states[key]
            posts = [
                p.id for _, p in self._rows_across_tenants(self._posts) if p.created_at < before
            ][:limit]
            for key in posts:
                del self._posts[key]
            return len(gone) + len(states) + len(posts)

    async def purge_tenant(self, org_id: UUID, limit: int) -> int:
        async with self._lock:
            purged = 0
            for table in (self._installations, self._states, self._posts):
                for key in [row.id for row in self._rows(table, org_id)][:limit]:  # type: ignore[arg-type]
                    del table[key]
                    purged += 1
            return purged
