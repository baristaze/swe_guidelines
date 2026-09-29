from collections.abc import Callable
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, delete, false, func, or_, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from acme.om.base import EMPTY_UUID, Identifiable, new_id
from acme.om.billing.storage.tables.billing_accounts import BillingAccounts
from acme.om.billing.types.account import BillingAccount
from acme.om.exceptions import Conflict, NotFound, UniqueKeyTaken
from acme.om.idempotency.storage.tables.idempotency_records import IdempotencyRecords
from acme.om.opcontext import CredentialKind, Role
from acme.om.outbox.storage.tables.outbox_rows import OutboxRows
from acme.om.outbox.types.row import OutboxRow
from acme.om.storage.impl.pg_base import (
    PLAN_WITH_VALUES,
    PgStorageBase,
    delete_batch,
    deleted,
    set_scope,
    violated_constraint,
)
from acme.om.storage.utils.translation import apply_row, to_model, to_row, to_values
from acme.om.tenancy.rules import email_digest, fold_email
from acme.om.tenancy.storage import TenancyStorageInterface
from acme.om.tenancy.storage.tables.api_keys import ApiKeys
from acme.om.tenancy.storage.tables.identities import Identities
from acme.om.tenancy.storage.tables.invitations import Invitations
from acme.om.tenancy.storage.tables.memberships import Memberships
from acme.om.tenancy.storage.tables.orgs import Orgs
from acme.om.tenancy.storage.tables.platform_sizes import PlatformSizes
from acme.om.tenancy.storage.tables.sessions import Sessions
from acme.om.tenancy.storage.tables.sign_in_delays import SignInDelays
from acme.om.tenancy.storage.tables.socket_tickets import SocketTickets
from acme.om.tenancy.storage.tables.users import Users
from acme.om.tenancy.types.api_key import ApiKey
from acme.om.tenancy.types.identity import Identity
from acme.om.tenancy.types.invitation import Invitation, InvitationState
from acme.om.tenancy.types.issued import OrgMembership
from acme.om.tenancy.types.membership import Membership
from acme.om.tenancy.types.org import Org
from acme.om.tenancy.types.session import Session
from acme.om.tenancy.types.sign_in_delay import SignInDelay
from acme.om.tenancy.types.size import PlatformSize
from acme.om.tenancy.types.socket_ticket import SocketTicket
from acme.om.tenancy.types.user import User


class TenancyStoragePostgresImpl(PgStorageBase, TenancyStorageInterface):
    async def read_identity(self, identity_id: UUID) -> Identity | None:
        stmt = select(Identities).where(Identities.id == identity_id)
        async with self._session_for(stmt, org_id=EMPTY_UUID) as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            return None if row is None else to_model(row, Identity)

    async def read_identity_by_email_digest(self, email_digest: str) -> Identity | None:
        stmt = select(Identities).where(Identities.email_digest == email_digest)
        async with self._session_for(stmt, org_id=EMPTY_UUID) as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            return None if row is None else to_model(row, Identity)

    async def read_identity_by_issuer_subject(self, issuer: str, subject: str) -> Identity | None:
        stmt = select(Identities).where(Identities.issuer == issuer, Identities.subject == subject)
        async with self._session_for(stmt, org_id=EMPTY_UUID) as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            return None if row is None else to_model(row, Identity)

    async def write_identity(
        self, identity: Identity, outbox_rows: tuple[OutboxRow, ...] = ()
    ) -> None:
        if not outbox_rows:
            await self._upsert_global(Identities, identity)
            return
        # The identity and its audit rows in one commit, under the system
        # scope, which is the one the rows belong to: no tenant holds them.
        async with self._session_for(Identities, org_id=EMPTY_UUID) as session:
            row = await session.get(Identities, identity.id)
            if row is None:
                session.add(to_row(identity, Identities))
            else:
                apply_row(row, identity)
            for outbox_row in outbox_rows:
                session.add(to_row(outbox_row, OutboxRows, org_id=EMPTY_UUID))
            try:
                await session.commit()
            except IntegrityError as error:
                raise UniqueKeyTaken(
                    f"identities {identity.id}: "
                    f"{violated_constraint(error) or 'a unique key'} is taken"
                ) from error

    async def disable_operator(
        self, identity: Identity, outbox_rows: tuple[OutboxRow, ...], at: datetime
    ) -> int:
        # The entry, its audit, and the end of the operator's live credentials
        # in one commit under the system scope, where every one of them is.
        ended = (
            update(Sessions)
            .where(
                Sessions.org_id == EMPTY_UUID,
                Sessions.identity_id == identity.id,
                Sessions.revoked_at.is_(None),
                Sessions.expires_at > at,
                or_(
                    Sessions.credential_kind == CredentialKind.OPERATOR_TOKEN.value,
                    Sessions.second_factor_at.is_not(None),
                ),
            )
            .values(revoked_at=at, updated_at=at, updated_by=EMPTY_UUID)
            .returning(Sessions.id)
        )
        async with self._session_for(Identities, org_id=EMPTY_UUID) as session:
            row = await session.get(Identities, identity.id)
            if row is None:
                raise NotFound(f"identity {identity.id} not found")
            apply_row(row, identity)
            for outbox_row in outbox_rows:
                session.add(to_row(outbox_row, OutboxRows, org_id=EMPTY_UUID))
            await session.flush()
            count = len((await session.execute(ended)).all())
            await session.commit()
            return count

    @staticmethod
    async def _matched(session: AsyncSession, stmt: Any) -> bool:
        """Runs one conditional statement and commits; whether it matched."""
        matched = (await session.execute(stmt)).first() is not None
        await session.commit()
        return matched

    async def write_totp_secret(self, identity_id: UUID, sealed: str, at: datetime) -> bool:
        stmt = (
            update(Identities)
            .where(Identities.id == identity_id, Identities.totp_confirmed_at.is_(None))
            .values(totp_secret=sealed, totp_last_step=None, updated_at=at)
            .returning(Identities.id)
        )
        async with self._session_for(stmt, org_id=EMPTY_UUID) as session:
            return await self._matched(session, stmt)

    async def write_time_zone(self, identity_id: UUID, time_zone: str, at: datetime) -> bool:
        stmt = (
            update(Identities)
            .where(Identities.id == identity_id)
            .values(time_zone=time_zone, updated_at=at)
            .returning(Identities.id)
        )
        async with self._session_for(stmt, org_id=EMPTY_UUID) as session:
            return await self._matched(session, stmt)

    async def confirm_totp(self, identity_id: UUID, step: int, at: datetime) -> bool:
        stmt = (
            update(Identities)
            .where(
                Identities.id == identity_id,
                Identities.totp_secret.is_not(None),
                Identities.totp_confirmed_at.is_(None),
            )
            .values(totp_confirmed_at=at, totp_last_step=step, updated_at=at)
            .returning(Identities.id)
        )
        async with self._session_for(stmt, org_id=EMPTY_UUID) as session:
            return await self._matched(session, stmt)

    async def use_totp_step(self, identity_id: UUID, step: int) -> bool:
        stmt = (
            update(Identities)
            .where(
                Identities.id == identity_id,
                Identities.totp_confirmed_at.is_not(None),
                or_(Identities.totp_last_step.is_(None), Identities.totp_last_step < step),
            )
            .values(totp_last_step=step)
            .returning(Identities.id)
        )
        async with self._session_for(stmt, org_id=EMPTY_UUID) as session:
            return await self._matched(session, stmt)

    async def read_sign_in_delay(self, email_digest: str) -> SignInDelay | None:
        stmt = select(SignInDelays).where(SignInDelays.email_digest == email_digest)
        async with self._session_for(stmt, org_id=EMPTY_UUID) as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            return None if row is None else to_model(row, SignInDelay)

    async def record_failed_sign_in(self, email_digest: str, at: datetime) -> None:
        # The count moves in the statement, so guesses made at once are each
        # counted rather than one read and written back by all of them.
        stmt = (
            insert(SignInDelays)
            .values(id=new_id(), email_digest=email_digest, failures=1, last_failed_at=at)
            .on_conflict_do_update(
                index_elements=[SignInDelays.email_digest],
                set_={"failures": SignInDelays.failures + 1, "last_failed_at": at},
            )
        )
        async with self._session_for(stmt, org_id=EMPTY_UUID) as session:
            await session.execute(stmt)
            await session.commit()

    async def clear_failed_sign_ins(self, email_digest: str) -> None:
        stmt = delete(SignInDelays).where(SignInDelays.email_digest == email_digest)
        async with self._session_for(stmt, org_id=EMPTY_UUID) as session:
            await session.execute(stmt)
            await session.commit()

    async def purge_sign_in_delays(self, before: datetime, limit: int) -> int:
        stmt = delete_batch(SignInDelays, SignInDelays.last_failed_at < before, limit=limit)
        async with self._session_for(stmt, org_id=EMPTY_UUID) as session:
            purged = deleted(await session.execute(stmt))
            await session.commit()
            return purged

    async def read_org(self, org_id: UUID) -> Org | None:
        stmt = select(Orgs).where(Orgs.org_id == org_id, Orgs.id == org_id)
        async with self._session_for(stmt, org_id=org_id) as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            return None if row is None else to_model(row, Org)

    async def read_org_by_slug(self, slug: str) -> Org | None:
        stmt = select(Orgs).where(Orgs.slug == slug, Orgs.deleted_at.is_(None))
        async with self._session_for(stmt, org_id=EMPTY_UUID) as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            return None if row is None else to_model(row, Org)

    async def count_orgs(self) -> int:
        stmt = select(func.count()).select_from(Orgs).where(Orgs.deleted_at.is_(None))
        async with self._session_for(stmt, org_id=EMPTY_UUID) as session:
            return (await session.execute(stmt)).scalar_one()

    async def count_orgs_and_users(self) -> tuple[int, int]:
        orgs = select(func.count()).select_from(Orgs).where(Orgs.deleted_at.is_(None))
        users = select(func.count()).select_from(Users).where(Users.deleted_at.is_(None))
        stmt = select(orgs.scalar_subquery(), users.scalar_subquery())
        async with self._session_for(stmt, org_id=EMPTY_UUID) as session:
            found = (await session.execute(stmt)).one()
            return found[0], found[1]

    async def write_platform_size(self, size: PlatformSize) -> None:
        # One statement: the row is written, or rewritten by a newer count.
        values = to_values(size, PlatformSizes)
        stmt = (
            insert(PlatformSizes)
            .values(id=EMPTY_UUID, **values)
            .on_conflict_do_update(
                index_elements=[PlatformSizes.id],
                set_=values,
                where=PlatformSizes.counted_at < size.counted_at,
            )
        )
        async with self._session_for(stmt, org_id=EMPTY_UUID) as session:
            await session.execute(stmt)
            await session.commit()

    async def read_platform_size(self) -> PlatformSize | None:
        stmt = select(PlatformSizes).where(PlatformSizes.id == EMPTY_UUID)
        async with self._session_for(stmt, org_id=EMPTY_UUID) as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            return None if row is None else to_model(row, PlatformSize)

    async def read_orgs(self, limit: int, after_id: UUID | None = None) -> list[Org]:
        stmt = select(Orgs).order_by(Orgs.id).limit(limit)
        if after_id is not None:
            stmt = stmt.where(Orgs.id > after_id)
        async with self._session_for(stmt, org_id=EMPTY_UUID) as session:
            result = await session.execute(stmt)
            return [to_model(row, Org) for row in result.scalars()]

    async def mark_org_purged(self, org_id: UUID, purged_at: datetime) -> bool:
        stmt = (
            update(Orgs)
            .where(
                Orgs.org_id == org_id,
                Orgs.id == org_id,
                Orgs.deleted_at.is_not(None),
                Orgs.purged_at.is_(None),
            )
            .values(purged_at=purged_at)
        )
        async with self._session_for(stmt, org_id=org_id) as session:
            marked = deleted(await session.execute(stmt)) == 1
            await session.commit()
            return marked

    async def write_org(
        self, org_id: UUID, org: Org, outbox_rows: tuple[OutboxRow, ...] = ()
    ) -> None:
        await self._upsert(Orgs, org_id, org, outbox_rows)

    async def create_org_with_owner(
        self,
        org_id: UUID,
        org: Org,
        user: User,
        membership: Membership,
        identity: Identity | None = None,
        personal: tuple[Org, User, Membership] | None = None,
    ) -> None:
        await self._create_together(
            org_id,
            (Orgs, org),
            (Users, user),
            (Memberships, membership),
            identity=identity,
            personal=personal,
        )

    async def create_member(
        self,
        org_id: UUID,
        user: User,
        membership: Membership,
        outbox_rows: tuple[OutboxRow, ...],
        identity: Identity | None = None,
        personal: tuple[Org, User, Membership] | None = None,
        invitation: Invitation | None = None,
    ) -> None:
        await self._create_together(
            org_id,
            (Users, user),
            (Memberships, membership),
            *((OutboxRows, row) for row in outbox_rows),
            identity=identity,
            personal=personal,
            invitation=invitation,
        )

    async def _create_together(
        self,
        org_id: UUID,
        *rows: tuple[type[Any], Identifiable],
        identity: Identity | None = None,
        personal: tuple[Org, User, Membership] | None = None,
        invitation: Invitation | None = None,
    ) -> None:
        """The rows land in one commit or not at all; every table is in the
        core role, which the session's role routing holds. The identity, when
        given, is written in the same commit: inserted when new, updated when
        it exists (the global table has no tenant to check). A new person's
        personal org, when given, lands first under its own tenant, then the
        scope is set again to `org_id` for the rest, so each statement is
        fenced by the tenant it touches, as a switch's two sessions are. The
        invitation, when given, is the tenant's existing row, updated in the
        same commit; one that is not in the tenant lands nothing. A violated
        key is UniqueKeyTaken, never a driver error."""
        row_type = rows[0][0]
        first_org_id = org_id if personal is None else personal[0].id
        async with self._session_for(row_type, org_id=first_org_id) as session:
            if identity is not None:
                existing = await session.get(Identities, identity.id)
                if existing is None:
                    session.add(to_row(identity, Identities))
                else:
                    apply_row(existing, identity)
            try:
                if personal is not None:
                    tenant, owner, owns = personal
                    session.add(to_row(tenant, Orgs, org_id=tenant.id))
                    session.add(to_row(owner, Users, org_id=tenant.id))
                    session.add(to_row(owns, Memberships, org_id=tenant.id))
                    await session.flush()
                    await set_scope(session, org_id, None, None)
                for table, entity in rows:
                    session.add(to_row(entity, table, org_id=org_id))
                if invitation is not None:
                    stored = await session.get(Invitations, invitation.id)
                    if stored is None or stored.org_id != org_id:
                        await session.rollback()
                        raise NotFound(f"invitation {invitation.id} is not in {org_id}")
                    apply_row(stored, invitation)
                await session.commit()
            except IntegrityError as error:
                await session.rollback()
                raise UniqueKeyTaken(
                    f"{row_type.__tablename__} and its siblings: "
                    f"{violated_constraint(error) or 'a unique key'} is taken"
                ) from error

    async def remove_member(
        self,
        org_id: UUID,
        user: User,
        membership: Membership,
        outbox_rows: tuple[OutboxRow, ...],
        revocation_row: Callable[[str, UUID], OutboxRow],
    ) -> tuple[OutboxRow, ...]:
        # Two updates, the revocation of every credential of the user, and the
        # outbox rows in one commit; a row that is missing or another tenant's
        # lands nothing. Both answer the same way: a caller holding an id of
        # another tenant is told what a caller holding an id that never
        # existed is told, so the answer carries no word about whether the row
        # is out there under someone else.
        at, by = user.deleted_at, user.deleted_by
        if at is None or by is None:
            raise ValueError("remove_member lands a user that is deleted")
        revoked: list[OutboxRow] = []
        async with self._session_for(Users, org_id=org_id) as session:
            for table, entity in ((Users, user), (Memberships, membership)):
                row = await session.get(table, entity.id)
                if row is None or row.org_id != org_id:
                    raise NotFound(f"{table.__tablename__} {entity.id} is not in {org_id}")
                apply_row(row, entity)
            sessions = (
                update(Sessions)
                .where(
                    Sessions.org_id == org_id,
                    Sessions.user_id == user.id,
                    Sessions.revoked_at.is_(None),
                )
                .values(revoked_at=at, updated_at=at, updated_by=by)
                .returning(Sessions.id)
            )
            for session_id in (await session.execute(sessions)).scalars().all():
                revoked.append(revocation_row("tenancy.session.revoked", session_id))
            keys = (
                update(ApiKeys)
                .where(
                    ApiKeys.org_id == org_id,
                    ApiKeys.user_id == user.id,
                    ApiKeys.deleted_at.is_(None),
                )
                .values(deleted_at=at, deleted_by=by, updated_at=at, updated_by=by)
                .returning(ApiKeys.id)
            )
            for key_id in (await session.execute(keys)).scalars().all():
                revoked.append(revocation_row("tenancy.api_key.deleted", key_id))
            for outbox_row in (*outbox_rows, *revoked):
                session.add(to_row(outbox_row, OutboxRows, org_id=org_id))
            await session.commit()
        return tuple(revoked)

    async def write_closed_org(
        self,
        org_id: UUID,
        org: Org,
        outbox_rows: tuple[OutboxRow, ...],
        member_row: Callable[[User], OutboxRow],
        revocation_row: Callable[[str, UUID, UUID], OutboxRow],
    ) -> tuple[OutboxRow, ...]:
        # The org row, locked, then one statement per table, each naming the
        # tenant: a member added while this runs waits on the lock and then
        # finds the org it joins closed by the same commit.
        at, by = org.updated_at, org.updated_by
        ended = {"deleted_at": at, "deleted_by": by, "updated_at": at, "updated_by": by}
        async with self._session_for(Orgs, org_id=org_id) as session:
            row = (
                await session.execute(
                    select(Orgs).where(Orgs.id == org.id, Orgs.org_id == org_id).with_for_update()
                )
            ).scalar_one_or_none()
            if row is None or row.deleted_at is not None:
                await session.rollback()
                raise NotFound(f"org {org.id} is not live in {org_id}")
            apply_row(row, org)
            users = (
                (
                    await session.execute(
                        update(Users)
                        .where(Users.org_id == org_id, Users.deleted_at.is_(None))
                        .values(**ended)
                        .returning(Users)
                    )
                )
                .scalars()
                .all()
            )
            built = [member_row(to_model(user, User)) for user in users]
            await session.execute(
                update(Memberships)
                .where(Memberships.org_id == org_id, Memberships.deleted_at.is_(None))
                .values(**ended)
            )
            sessions = await session.execute(
                update(Sessions)
                .where(Sessions.org_id == org_id, Sessions.revoked_at.is_(None))
                .values(revoked_at=at, updated_at=at, updated_by=by)
                .returning(Sessions.id, Sessions.user_id)
            )
            for session_id, user_id in sessions.all():
                built.append(revocation_row("tenancy.session.revoked", session_id, user_id))
            keys = await session.execute(
                update(ApiKeys)
                .where(ApiKeys.org_id == org_id, ApiKeys.deleted_at.is_(None))
                .values(**ended)
                .returning(ApiKeys.id, ApiKeys.user_id)
            )
            for key_id, user_id in keys.all():
                built.append(revocation_row("tenancy.api_key.deleted", key_id, user_id))
            await session.execute(
                update(Invitations)
                .where(
                    Invitations.org_id == org_id,
                    Invitations.state == InvitationState.PENDING.value,
                )
                .values(state=InvitationState.REVOKED.value, updated_at=at, updated_by=by)
            )
            for outbox_row in (*outbox_rows, *built):
                session.add(to_row(outbox_row, OutboxRows, org_id=org_id))
            await session.commit()
        return tuple(built)

    async def delete_person(
        self,
        identity_id: UUID,
        email: str,
        owned: tuple[UUID, ...],
        outbox_rows: tuple[OutboxRow, ...],
        revocation_row: Callable[[UUID, str, UUID], OutboxRow],
    ) -> tuple[OutboxRow, ...]:
        # One transaction under the system scope: a person's rows are in every
        # tenant they joined, and the erasure is one commit or none, a hard
        # delete outside the sweep (ADR 0041). Every statement names the
        # person (the identity, its address, or the users it is) and, where
        # a row is a tenant's, the tenants those users are in.
        revoked: list[OutboxRow] = []
        async with self._session_for(Identities, org_id=EMPTY_UUID) as session:
            gone = await session.execute(
                delete(Identities).where(Identities.id == identity_id).returning(Identities.id)
            )
            if gone.first() is None:
                await session.rollback()
                raise NotFound(f"identity {identity_id} not found")
            if owned:
                # The owners of each tenant the person owns, locked: a second
                # owner leaving at once waits here and then finds only itself.
                owners = (
                    select(Memberships.org_id, Users.identity_id)
                    .join(Users, Users.id == Memberships.user_id)
                    .where(
                        Memberships.org_id.in_(owned),
                        Memberships.role == Role.OWNER.value,
                        Memberships.deleted_at.is_(None),
                        Users.deleted_at.is_(None),
                    )
                    .with_for_update(of=Memberships)
                )
                kept = {
                    org_id
                    for org_id, owner in (await session.execute(owners)).all()
                    if owner != identity_id
                }
                alone = [org_id for org_id in owned if org_id not in kept]
                if alone:
                    await session.rollback()
                    raise Conflict(f"no owner would be left in {', '.join(map(str, alone))}")
            users = (
                await session.execute(
                    delete(Users)
                    .where(Users.identity_id == identity_id)
                    .returning(Users.org_id, Users.id)
                )
            ).all()
            org_ids = {org_id for org_id, _ in users}
            user_ids = {user_id for _, user_id in users}
            await session.execute(
                delete(Memberships).where(
                    Memberships.org_id.in_(org_ids), Memberships.user_id.in_(user_ids)
                )
            )
            sessions = await session.execute(
                delete(Sessions)
                .where(
                    or_(
                        and_(Sessions.org_id.in_(org_ids), Sessions.user_id.in_(user_ids)),
                        and_(Sessions.org_id == EMPTY_UUID, Sessions.identity_id == identity_id),
                    )
                )
                .returning(Sessions.org_id, Sessions.id, Sessions.revoked_at)
            )
            for org_id, session_id, revoked_at in sessions.all():
                if org_id != EMPTY_UUID and revoked_at is None:
                    revoked.append(revocation_row(org_id, "tenancy.session.revoked", session_id))
            keys = await session.execute(
                delete(ApiKeys)
                .where(ApiKeys.org_id.in_(org_ids), ApiKeys.user_id.in_(user_ids))
                .returning(ApiKeys.org_id, ApiKeys.id, ApiKeys.deleted_at)
            )
            for org_id, key_id, deleted_at in keys.all():
                if deleted_at is None:
                    revoked.append(revocation_row(org_id, "tenancy.api_key.deleted", key_id))
            await session.execute(
                delete(SocketTickets).where(
                    SocketTickets.org_id.in_(org_ids), SocketTickets.user_id.in_(user_ids)
                )
            )
            # An invitation holds the address it was sent to: every one sent
            # to the person's, and every one they accepted, goes with them.
            await session.execute(
                delete(Invitations).where(
                    or_(
                        Invitations.email == fold_email(email),
                        and_(
                            Invitations.org_id.in_(org_ids),
                            Invitations.accepted_user_id.in_(user_ids),
                        ),
                    )
                )
            )
            await session.execute(
                delete(SignInDelays).where(SignInDelays.email_digest == email_digest(email))
            )
            for outbox_row in (*outbox_rows, *revoked):
                session.add(to_row(outbox_row, OutboxRows, org_id=outbox_row.org_id))
            await session.commit()
        return tuple(revoked)

    async def read_users(self, org_id: UUID, after: UUID | None, limit: int) -> list[User]:
        stmt = (
            select(Users)
            .where(Users.org_id == org_id, Users.deleted_at.is_(None))
            .order_by(Users.id)
            .limit(limit)
        )
        if after is not None:
            stmt = stmt.where(Users.id > after)  # is_after_in_id_order
        async with self._session_for(stmt, org_id=org_id) as session:
            result = await session.execute(stmt)
            return [to_model(row, User) for row in result.scalars()]

    async def read_user(self, org_id: UUID, user_id: UUID) -> User | None:
        stmt = select(Users).where(Users.org_id == org_id, Users.id == user_id)
        async with self._session_for(stmt, org_id=org_id) as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            return None if row is None else to_model(row, User)

    async def read_users_by_identity(
        self, identity_id: UUID, limit: int
    ) -> list[tuple[UUID, User]]:
        stmt = (
            select(Users)
            .where(Users.identity_id == identity_id, Users.deleted_at.is_(None))
            .order_by(Users.id)
            .limit(limit)
        )
        async with self._session_for(stmt, org_id=EMPTY_UUID) as session:
            result = await session.execute(stmt)
            return [(row.org_id, to_model(row, User)) for row in result.scalars()]

    async def read_memberships_by_identity(
        self, identity_id: UUID, limit: int, after_user_id: UUID | None = None
    ) -> list[OrgMembership]:
        # One statement over three tables of the core role, the living only,
        # so the bound cuts the rows the caller keeps.
        stmt = (
            select(Users, Orgs, Memberships)
            .join(Orgs, (Orgs.org_id == Users.org_id) & (Orgs.id == Users.org_id))
            .join(
                Memberships,
                (Memberships.org_id == Users.org_id) & (Memberships.user_id == Users.id),
            )
            .where(
                Users.identity_id == identity_id,
                Users.deleted_at.is_(None),
                Orgs.deleted_at.is_(None),
                Memberships.deleted_at.is_(None),
            )
            .order_by(Users.id)
            .limit(limit)
        )
        if after_user_id is not None:
            stmt = stmt.where(Users.id > after_user_id)  # is_after_in_id_order
        async with self._session_for(stmt, org_id=EMPTY_UUID) as session:
            result = await session.execute(stmt)
            return [
                OrgMembership(
                    org=to_model(org, Org),
                    user=to_model(user, User),
                    role=to_model(membership, Membership).role,
                )
                for user, org, membership in result.tuples()
            ]

    async def write_user(
        self, org_id: UUID, user: User, outbox_rows: tuple[OutboxRow, ...] = ()
    ) -> None:
        try:
            await self._upsert(Users, org_id, user, outbox_rows)
        except UniqueKeyTaken as error:
            # uq_users_org_id_identity_id_live: one live user per identity in a tenant.
            raise UniqueKeyTaken(
                f"identity {user.identity_id} already has a live user in this org"
            ) from error

    async def read_memberships(
        self, org_id: UUID, limit: int, after_user_id: UUID | None = None
    ) -> list[Membership]:
        # uq_memberships_org_id_user_id covers the live rows in this order.
        stmt = (
            select(Memberships)
            .where(Memberships.org_id == org_id, Memberships.deleted_at.is_(None))
            .order_by(Memberships.user_id)
            .limit(limit)
        )
        if after_user_id is not None:
            stmt = stmt.where(Memberships.user_id > after_user_id)  # is_after_in_id_order
        async with self._session_for(stmt, org_id=org_id) as session:
            result = await session.execute(stmt)
            return [to_model(row, Membership) for row in result.scalars()]

    async def count_members(self, org_id: UUID, role: Role | None = None) -> int:
        stmt = (
            select(func.count())
            .select_from(Memberships)
            .where(Memberships.org_id == org_id, Memberships.deleted_at.is_(None))
        )
        if role is not None:
            stmt = stmt.where(Memberships.role == role.value)
        async with self._session_for(stmt, org_id=org_id) as session:
            return (await session.execute(stmt)).scalar_one()

    async def read_membership_for_user(self, org_id: UUID, user_id: UUID) -> Membership | None:
        stmt = select(Memberships).where(
            Memberships.org_id == org_id,
            Memberships.user_id == user_id,
            Memberships.deleted_at.is_(None),
        )
        async with self._session_for(stmt, org_id=org_id, user_id=user_id) as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            return None if row is None else to_model(row, Membership)

    async def read_principal(
        self, org_id: UUID, user_id: UUID, seen: tuple[UUID, datetime] | None = None
    ) -> tuple[Org | None, User | None, Membership | None]:
        # The rows of `read_org`, `read_user`, and `read_membership_for_user`
        # in one statement under the tenant and the user: the org, and beside
        # it the user and the live membership when they exist.
        stmt = (
            select(Orgs, Users, Memberships)
            .select_from(Orgs)
            .outerjoin(Users, and_(Users.org_id == Orgs.org_id, Users.id == user_id))
            .outerjoin(
                Memberships,
                and_(
                    Memberships.org_id == Orgs.org_id,
                    Memberships.user_id == user_id,
                    Memberships.deleted_at.is_(None),
                ),
            )
            .where(Orgs.org_id == org_id, Orgs.id == org_id)
        )
        async with self._session_for(Orgs, org_id=org_id, user_id=user_id) as session:
            found = (await session.execute(stmt)).one_or_none()
            if seen is not None:
                # The session's use rides the principal's transaction instead
                # of opening one of its own.
                session_id, seen_at = seen
                await session.execute(
                    update(Sessions)
                    .where(
                        Sessions.org_id == org_id,
                        Sessions.id == session_id,
                        Sessions.user_id == user_id,
                        Sessions.revoked_at.is_(None),
                    )
                    .values(last_seen_at=seen_at)
                )
                await session.commit()
            if found is None:
                return None, None, None
            org, user, membership = found
            return (
                to_model(org, Org),
                None if user is None else to_model(user, User),
                None if membership is None else to_model(membership, Membership),
            )

    async def read_key_principal(
        self, org_id: UUID, user_id: UUID
    ) -> tuple[Org | None, User | None, Membership | None, BillingAccount | None]:
        # The principal's statement with the org's billing account joined on:
        # a table of the same role and the same tenant fence, read under the
        # same scope, so the join crosses neither.
        stmt = (
            select(Orgs, Users, Memberships, BillingAccounts)
            .select_from(Orgs)
            .outerjoin(Users, and_(Users.org_id == Orgs.org_id, Users.id == user_id))
            .outerjoin(
                Memberships,
                and_(
                    Memberships.org_id == Orgs.org_id,
                    Memberships.user_id == user_id,
                    Memberships.deleted_at.is_(None),
                ),
            )
            .outerjoin(BillingAccounts, BillingAccounts.org_id == Orgs.org_id)
            .where(Orgs.org_id == org_id, Orgs.id == org_id)
        )
        async with self._session_for(stmt, org_id=org_id, user_id=user_id) as session:
            found = (await session.execute(stmt)).one_or_none()
            if found is None:
                return None, None, None, None
            org, user, membership, account = found
            return (
                to_model(org, Org),
                None if user is None else to_model(user, User),
                None if membership is None else to_model(membership, Membership),
                None if account is None else to_model(account, BillingAccount),
            )

    async def write_membership(
        self, org_id: UUID, membership: Membership, outbox_rows: tuple[OutboxRow, ...] = ()
    ) -> None:
        await self._upsert(Memberships, org_id, membership, outbox_rows)

    async def read_sessions(
        self, org_id: UUID, user_id: UUID, live_at: datetime, limit: int
    ) -> list[Session]:
        stmt = (
            select(Sessions)
            .where(
                Sessions.org_id == org_id,
                Sessions.user_id == user_id,
                Sessions.revoked_at.is_(None),
                Sessions.expires_at > live_at,
            )
            .order_by(Sessions.id.desc())
            .limit(limit)
        )
        async with self._session_for(stmt, org_id=org_id, user_id=user_id) as session:
            result = await session.execute(stmt)
            return [to_model(row, Session) for row in result.scalars()]

    async def read_session(self, org_id: UUID, session_id: UUID) -> Session | None:
        stmt = select(Sessions).where(Sessions.org_id == org_id, Sessions.id == session_id)
        async with self._session_for(stmt, org_id=org_id) as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            return None if row is None else to_model(row, Session)

    async def read_session_by_digest(self, token_hash: str) -> tuple[UUID, Session] | None:
        stmt = select(Sessions).where(Sessions.token_hash == token_hash)
        async with self._session_for(stmt, org_id=EMPTY_UUID) as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            return None if row is None else (row.org_id, to_model(row, Session))

    async def read_session_with_identity_by_digest(
        self, token_hash: str
    ) -> tuple[UUID, Session, Identity | None] | None:
        stmt = (
            select(Sessions, Identities)
            .outerjoin(Identities, Identities.id == Sessions.identity_id)
            .where(Sessions.token_hash == token_hash)
        )
        async with self._session_for(stmt, org_id=EMPTY_UUID) as session:
            found = (await session.execute(stmt)).one_or_none()
            if found is None:
                return None
            row, identity = found
            return (
                row.org_id,
                to_model(row, Session),
                None if identity is None else to_model(identity, Identity),
            )

    async def read_operator_tokens(
        self, identity_id: UUID, live_at: datetime, after: UUID | None, limit: int
    ) -> list[Session]:
        stmt = (
            select(Sessions)
            .where(
                Sessions.org_id == EMPTY_UUID,
                Sessions.expires_at > live_at,
                Sessions.identity_id == identity_id,
                Sessions.credential_kind == CredentialKind.OPERATOR_TOKEN.value,
                Sessions.revoked_at.is_(None),
            )
            .order_by(Sessions.id.desc())
            .limit(limit)
        )
        if after is not None:
            stmt = stmt.where(Sessions.id < after)  # is_after_newest_first
        async with self._session_for(stmt, org_id=EMPTY_UUID) as session:
            result = await session.execute(stmt)
            return [to_model(row, Session) for row in result.scalars()]

    async def read_session_by_id(self, session_id: UUID) -> tuple[UUID, Session] | None:
        stmt = select(Sessions).where(Sessions.id == session_id)
        async with self._session_for(stmt, org_id=EMPTY_UUID) as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            return None if row is None else (row.org_id, to_model(row, Session))

    async def create_session(self, org_id: UUID, session: Session) -> bool:
        return await self._insert(Sessions, org_id, session)

    async def write_session(
        self, org_id: UUID, session: Session, outbox_rows: tuple[OutboxRow, ...] = ()
    ) -> None:
        await self._upsert(Sessions, org_id, session, outbox_rows)

    async def replace_session(
        self,
        org_id: UUID,
        session: Session,
        ended_org_id: UUID,
        ended: Session,
        outbox_rows: tuple[OutboxRow, ...],
    ) -> None:
        # One transaction, two tenants, and never the system scope: the ended
        # session is revoked under its own tenant, then the scope is set again
        # to the new session's tenant for its insert, so each statement is
        # fenced by the tenant it touches. The row lock makes a second switch
        # on the same session wait and then find it ended.
        async with self._session_for(Sessions, org_id=ended_org_id) as db:
            row = await db.get(Sessions, ended.id, with_for_update=True)
            if row is None or row.org_id != ended_org_id:
                raise NotFound(f"session {ended.id} is not in {ended_org_id}")
            if row.revoked_at is not None:
                raise Conflict(f"session {ended.id} has already ended")
            apply_row(row, ended)
            for outbox_row in outbox_rows:
                db.add(to_row(outbox_row, OutboxRows, org_id=ended_org_id))
            try:
                await db.flush()
                await set_scope(db, org_id, None, None)
                db.add(to_row(session, Sessions, org_id=org_id))
                await db.commit()
            except IntegrityError as error:
                await db.rollback()
                taken = violated_constraint(error) or "a unique key"
                raise UniqueKeyTaken(f"sessions {session.id}: {taken} is taken") from error

    async def exchange_sign_in(self, org_id: UUID, session: Session, ended: Session) -> None:
        # One transaction on the system login, which the sign-in's row needs:
        # the sign-in is ended under the system scope, then the scope is set
        # to the new session's tenant for its insert, so the insert is fenced
        # by the tenant it lands in. The row lock makes a second exchange of
        # the same sign-in wait and then find it ended.
        async with self._session_for(Sessions, org_id=EMPTY_UUID) as db:
            row = await db.get(Sessions, ended.id, with_for_update=True)
            if row is None or row.org_id != EMPTY_UUID:
                raise NotFound(f"session {ended.id} is not a sign-in")
            if row.revoked_at is not None:
                raise Conflict(f"sign-in {ended.id} was exchanged already")
            apply_row(row, ended)
            try:
                await db.flush()
                await set_scope(db, org_id, None, None)
                db.add(to_row(session, Sessions, org_id=org_id))
                await db.commit()
            except IntegrityError as error:
                await db.rollback()
                taken = violated_constraint(error) or "a unique key"
                raise UniqueKeyTaken(f"sessions {session.id}: {taken} is taken") from error

    async def read_api_keys(
        self, org_id: UUID, after: UUID | None, limit: int, user_id: UUID | None = None
    ) -> list[ApiKey]:
        stmt = (
            select(ApiKeys)
            .where(ApiKeys.org_id == org_id, ApiKeys.deleted_at.is_(None))
            .order_by(ApiKeys.id.desc())
            .limit(limit)
        )
        if user_id is not None:
            stmt = stmt.where(ApiKeys.user_id == user_id)
        if after is not None:
            stmt = stmt.where(ApiKeys.id < after)  # is_after_newest_first
        async with self._session_for(stmt, org_id=org_id, user_id=user_id) as session:
            result = await session.execute(stmt)
            return [to_model(row, ApiKey) for row in result.scalars()]

    async def read_api_key(self, org_id: UUID, api_key_id: UUID) -> ApiKey | None:
        stmt = select(ApiKeys).where(ApiKeys.org_id == org_id, ApiKeys.id == api_key_id)
        async with self._session_for(stmt, org_id=org_id) as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            return None if row is None else to_model(row, ApiKey)

    async def read_api_key_by_digest(self, key_hash: str) -> tuple[UUID, ApiKey] | None:
        stmt = select(ApiKeys).where(ApiKeys.key_hash == key_hash)
        async with self._session_for(stmt, org_id=EMPTY_UUID) as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            return None if row is None else (row.org_id, to_model(row, ApiKey))

    async def issue_api_key(
        self,
        org_id: UUID,
        api_key: ApiKey,
        outbox_rows: tuple[OutboxRow, ...],
        attempt_id: UUID | None,
    ) -> tuple[ApiKey, bool]:
        if await self._insert(ApiKeys, org_id, api_key, outbox_rows):
            return api_key, True
        if attempt_id is None:
            # No key, no marker, nothing holding this attempt: nothing to fence
            # the re-mint with, so there is no re-mint.
            raise Conflict(f"api key {api_key.id} cannot be re-minted")
        # The rerun: one conditional statement re-mints the secret on the
        # issuer's row, with both fences of the interface in its own WHERE. The
        # second reads the marker; both tables are in the `core` role, so the
        # statement stays inside one role and one commit.
        held = (
            select(IdempotencyRecords.id)
            .where(
                IdempotencyRecords.org_id == org_id,
                IdempotencyRecords.target_id == api_key.id,
                IdempotencyRecords.attempt_id == attempt_id,
                IdempotencyRecords.status.is_(None),
            )
            .exists()
        )
        stmt = (
            update(ApiKeys)
            .where(
                ApiKeys.org_id == org_id,
                ApiKeys.id == api_key.id,
                ApiKeys.user_id == api_key.user_id,
                ApiKeys.deleted_at.is_(None),
                held,
            )
            .values(
                key_hash=api_key.key_hash,
                updated_at=api_key.updated_at,
                updated_by=api_key.updated_by,
            )
            .returning(ApiKeys)
        )
        async with self._session_for(stmt, org_id=org_id) as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row is None:
                raise Conflict(f"api key {api_key.id} cannot be re-minted")
            reissued = to_model(row, ApiKey)
            await session.commit()
            return reissued, False

    async def write_api_key(
        self, org_id: UUID, api_key: ApiKey, outbox_rows: tuple[OutboxRow, ...] = ()
    ) -> None:
        await self._upsert(ApiKeys, org_id, api_key, outbox_rows)

    async def purge_deleted(self, before: datetime, tickets_before: datetime, limit: int) -> int:
        gone_users = delete_batch(Users, Users.deleted_at < before, limit=limit).returning(
            Users.org_id, Users.id
        )
        # Every tenant's rows past the retention, and the sign-in sessions of
        # the system scope with them, so the system scope, spelled here.
        async with self._session_for(gone_users, org_id=EMPTY_UUID) as session:
            # Planned with its values, so each index serves an idle pass too.
            await session.execute(PLAN_WITH_VALUES)
            # The users travel back with their tenants: their memberships go
            # with them.
            gone = (await session.execute(gone_users)).all()
            purged = len(gone)
            of_gone_users = (
                and_(
                    Memberships.org_id.in_({org_id for org_id, _ in gone}),
                    tuple_(Memberships.org_id, Memberships.user_id).in_(
                        [(org_id, user_id) for org_id, user_id in gone]
                    ),
                )
                if gone
                else false()
            )
            for stmt in (
                delete_batch(
                    Memberships,
                    or_(of_gone_users, Memberships.deleted_at < before),
                    limit=limit,
                ),
                delete_batch(
                    ApiKeys,
                    or_(ApiKeys.deleted_at < before, ApiKeys.expires_at < before),
                    limit=limit,
                ),
                # A revoked session expires within its lifetime, so the expiry
                # alone decides.
                delete_batch(Sessions, Sessions.expires_at < before, limit=limit),
                # A ticket is spent within the minute it lives, so the same.
                delete_batch(SocketTickets, SocketTickets.expires_at < tickets_before, limit=limit),
                delete_batch(
                    Invitations,
                    or_(
                        and_(
                            Invitations.state != InvitationState.PENDING.value,
                            Invitations.updated_at < before,
                        ),
                        Invitations.expires_at < before,
                    ),
                    limit=limit,
                ),
            ):
                purged += deleted(await session.execute(stmt))
            await session.commit()
        return purged

    async def read_invitation(self, org_id: UUID, invitation_id: UUID) -> Invitation | None:
        stmt = select(Invitations).where(
            Invitations.org_id == org_id, Invitations.id == invitation_id
        )
        async with self._session_for(stmt, org_id=org_id) as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            return None if row is None else to_model(row, Invitation)

    async def read_invitation_by_provider_id(
        self, org_id: UUID, provider_invitation_id: str
    ) -> Invitation | None:
        stmt = select(Invitations).where(
            Invitations.org_id == org_id,
            Invitations.provider_invitation_id == provider_invitation_id,
        )
        async with self._session_for(stmt, org_id=org_id) as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            return None if row is None else to_model(row, Invitation)

    async def read_pending_invitation(self, org_id: UUID, email: str) -> Invitation | None:
        stmt = select(Invitations).where(
            Invitations.org_id == org_id,
            Invitations.email == fold_email(email),
            Invitations.state == InvitationState.PENDING.value,
        )
        async with self._session_for(stmt, org_id=org_id) as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            return None if row is None else to_model(row, Invitation)

    async def read_invitations(
        self, org_id: UUID, after: UUID | None, limit: int
    ) -> list[Invitation]:
        # Mirrors `is_after_newest_first`: by id descending, strictly before the cursor.
        stmt = (
            select(Invitations)
            .where(
                Invitations.org_id == org_id,
                Invitations.state == InvitationState.PENDING.value,
            )
            .order_by(Invitations.id.desc())
            .limit(limit)
        )
        if after is not None:
            stmt = stmt.where(Invitations.id < after)
        async with self._session_for(stmt, org_id=org_id) as session:
            result = await session.execute(stmt)
            return [to_model(row, Invitation) for row in result.scalars()]

    async def write_invitation(
        self, org_id: UUID, invitation: Invitation, outbox_rows: tuple[OutboxRow, ...] = ()
    ) -> None:
        await self._upsert(Invitations, org_id, invitation, outbox_rows)

    async def purge_tenant(self, org_id: UUID, limit: int) -> int:
        purged = 0
        async with self._session_for(Users, org_id=org_id) as session:
            for table in (Users, Memberships, ApiKeys, Sessions, SocketTickets, Invitations):
                stmt = delete_batch(table, table.org_id == org_id, limit=limit)
                purged += deleted(await session.execute(stmt))
            await session.commit()
        return purged

    async def create_socket_ticket(self, org_id: UUID, ticket: SocketTicket) -> bool:
        return await self._insert(SocketTickets, org_id, ticket)

    async def redeem_socket_ticket(
        self, ticket_hash: str, redeemed_at: datetime
    ) -> tuple[UUID, SocketTicket] | None:
        stmt = (
            update(SocketTickets)
            .where(SocketTickets.ticket_hash == ticket_hash, SocketTickets.redeemed_at.is_(None))
            .values(redeemed_at=redeemed_at)
            .returning(SocketTickets)
        )
        async with self._session_for(stmt, org_id=EMPTY_UUID) as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row is None:
                return None
            consumed = (row.org_id, to_model(row, SocketTicket))
            await session.commit()
            return consumed
