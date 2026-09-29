from datetime import datetime
from uuid import UUID

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError

from acme.om.base import EMPTY_UUID
from acme.om.exceptions import UniqueKeyTaken
from acme.om.outbox.types.row import OutboxRow
from acme.om.slack.storage import SlackStorageInterface
from acme.om.slack.storage.tables.slack import (
    SlackInstallations,
    SlackInstallStates,
    SlackPosts,
)
from acme.om.slack.types.installation import SlackInstallation, SlackInstallState, SlackPost
from acme.om.storage.impl.pg_base import (
    PLAN_WITH_VALUES,
    PgStorageBase,
    delete_batch,
    deleted,
    violated_constraint,
)
from acme.om.storage.utils.translation import to_model, to_row


class SlackStoragePostgresImpl(PgStorageBase, SlackStorageInterface):
    async def read_installation(self, org_id: UUID) -> SlackInstallation | None:
        stmt = select(SlackInstallations).where(
            SlackInstallations.org_id == org_id, SlackInstallations.deleted_at.is_(None)
        )
        async with self._session_for(stmt, org_id=org_id) as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            return None if row is None else to_model(row, SlackInstallation)

    async def read_installation_by_team(
        self, team_id: str
    ) -> tuple[UUID, SlackInstallation] | None:
        stmt = select(SlackInstallations).where(
            SlackInstallations.team_id == team_id, SlackInstallations.deleted_at.is_(None)
        )
        async with self._session_for(stmt, org_id=EMPTY_UUID) as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            return None if row is None else (row.org_id, to_model(row, SlackInstallation))

    async def write_installation(
        self,
        org_id: UUID,
        installation: SlackInstallation,
        outbox_rows: tuple[OutboxRow, ...],
    ) -> None:
        await self._upsert(SlackInstallations, org_id, installation, outbox_rows)

    async def claim_refresh(
        self, org_id: UUID, installation_id: UUID, now: datetime, until: datetime
    ) -> bool:
        stmt = (
            update(SlackInstallations)
            .where(
                SlackInstallations.org_id == org_id,
                SlackInstallations.id == installation_id,
                SlackInstallations.deleted_at.is_(None),
                or_(
                    SlackInstallations.refreshing_until.is_(None),
                    SlackInstallations.refreshing_until <= now,
                ),
            )
            .values(refreshing_until=until)
            .returning(SlackInstallations.id)
        )
        async with self._session_for(stmt, org_id=org_id) as session:
            claimed = (await session.execute(stmt)).scalar_one_or_none() is not None
            await session.commit()
            return claimed

    async def settle_refresh(
        self,
        org_id: UUID,
        installation_id: UUID,
        token_expires_at: datetime | None,
        now: datetime,
    ) -> None:
        stmt = (
            update(SlackInstallations)
            .where(
                SlackInstallations.org_id == org_id,
                SlackInstallations.id == installation_id,
                SlackInstallations.deleted_at.is_(None),
            )
            .values(token_expires_at=token_expires_at, refreshing_until=None)
        )
        async with self._session_for(stmt, org_id=org_id) as session:
            await session.execute(stmt)
            await session.commit()

    async def create_install_state(self, org_id: UUID, state: SlackInstallState) -> None:
        async with self._session_for(SlackInstallStates, org_id=org_id) as session:
            session.add(to_row(state, SlackInstallStates, org_id=org_id))
            try:
                await session.commit()
            except IntegrityError as error:
                raise UniqueKeyTaken(
                    f"slack_install_states {state.id}: "
                    f"{violated_constraint(error) or 'a key'} is taken"
                ) from error

    async def redeem_install_state(
        self, state_hash: str, now: datetime
    ) -> tuple[UUID, SlackInstallState] | None:
        stmt = (
            update(SlackInstallStates)
            .where(
                SlackInstallStates.state_hash == state_hash,
                SlackInstallStates.redeemed_at.is_(None),
                SlackInstallStates.expires_at > now,
            )
            .values(redeemed_at=now)
            .returning(SlackInstallStates)
        )
        async with self._session_for(stmt, org_id=EMPTY_UUID) as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row is None:
                return None
            redeemed = (row.org_id, to_model(row, SlackInstallState))
            await session.commit()
            return redeemed

    async def read_post(self, org_id: UUID, key: UUID) -> SlackPost | None:
        stmt = select(SlackPosts).where(SlackPosts.org_id == org_id, SlackPosts.key == key)
        async with self._session_for(stmt, org_id=org_id) as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            return None if row is None else to_model(row, SlackPost)

    async def create_post(self, org_id: UUID, post: SlackPost) -> bool:
        async with self._session_for(SlackPosts, org_id=org_id) as session:
            session.add(to_row(post, SlackPosts, org_id=org_id))
            try:
                await session.commit()
            except IntegrityError as error:
                await session.rollback()
                if violated_constraint(error) == "uq_slack_posts_org_id_key":
                    return False
                raise UniqueKeyTaken(
                    f"slack_posts {post.id}: {violated_constraint(error) or 'a key'} is taken"
                ) from error
            return True

    async def purge(self, before: datetime, limit: int) -> int:
        purged = 0
        statements = (
            delete_batch(SlackInstallations, SlackInstallations.deleted_at < before, limit=limit),
            delete_batch(
                SlackInstallStates,
                or_(
                    SlackInstallStates.expires_at < before,
                    SlackInstallStates.redeemed_at < before,
                ),
                limit=limit,
            ),
            delete_batch(SlackPosts, SlackPosts.created_at < before, limit=limit),
        )
        # Every tenant's rows past the retention, so the system scope, spelled
        # here; the three tables are one role's, so one transaction, planned
        # with its values so each index serves an idle pass too.
        async with self._session_for(SlackPosts, org_id=EMPTY_UUID) as session:
            await session.execute(PLAN_WITH_VALUES)
            for stmt in statements:
                purged += deleted(await session.execute(stmt))
            await session.commit()
        return purged

    async def purge_tenant(self, org_id: UUID, limit: int) -> int:
        purged = 0
        for table in (SlackInstallations, SlackInstallStates, SlackPosts):
            stmt = delete_batch(table, table.org_id == org_id, limit=limit)
            async with self._session_for(stmt, org_id=org_id) as session:
                purged += deleted(await session.execute(stmt))
                await session.commit()
        return purged
