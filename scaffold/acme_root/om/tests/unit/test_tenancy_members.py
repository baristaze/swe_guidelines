"""The members delegate over the memory storage: the roles of an org's
members, a removal and every credential it ends, a member's name, and the
pages of members and memberships. Invitations and single sign-on run over
the identity provider's twin, in `test_tenancy_identity_provider.py`."""

from collections.abc import Callable
from uuid import UUID

import pytest

from acme.infra.cache import CacheScope
from acme.infra.impl.local import InfraLocalImpl
from acme.integrations.identity.absent import IdentityProviderAbsentImpl
from acme.om.base import new_id, utcnow
from acme.om.change_events.storage.impl.memory import ChangeEventStorageMemoryImpl
from acme.om.context import Role
from acme.om.exceptions import (
    CredentialExpired,
    NotAuthorized,
    NotFound,
    PersonalOrgFixed,
    ValidationFailed,
)
from acme.om.outbox.impl.relay import OutboxRelayImpl
from acme.om.outbox.storage.impl.memory import OutboxStorageMemoryImpl
from acme.om.outbox.types.row import OutboxRow
from acme.om.root import build_tenancy
from acme.om.tenancy import TenancyManagerInterface
from acme.om.tenancy.impl.manager import TenancyOptions
from acme.om.tenancy.storage.impl.memory import TenancyStorageMemoryImpl
from acme.om.tenancy.types.membership import Membership
from acme.om.tenancy.types.org import OrgKind
from acme.om.tenancy.types.user import PERSONAL_FIELDS, User

from contracts.outbox_storage import claim_all
from contracts.tenancy import SpyRelay, add_member, make_manager, request, sign_in, signed_up


async def test_member_roles_are_capped_at_the_callers_role(
    manager: TenancyManagerInterface, storage: TenancyStorageMemoryImpl
) -> None:
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    owner = await sign_in(manager, "ann@example.test", org.id)
    bob = await add_member(storage, org.id, "bob@example.test", Role.ADMIN)
    cid = await add_member(storage, org.id, "cid@example.test", Role.VIEWER)
    admin = await sign_in(manager, "bob@example.test", org.id)

    promoted = await manager.members.update_membership_role(admin, cid.id, Role.MEMBER)
    assert promoted.role is Role.MEMBER
    assert promoted.updated_at > promoted.created_at
    assert (await sign_in(manager, "cid@example.test", org.id)).security.role is Role.MEMBER

    with pytest.raises(NotAuthorized):
        await manager.members.update_membership_role(admin, cid.id, Role.OWNER)
    with pytest.raises(NotAuthorized):
        await manager.members.update_membership_role(admin, owner.user_id, Role.VIEWER)
    with pytest.raises(ValidationFailed):
        await manager.members.update_membership_role(admin, bob.id, Role.MEMBER)
    with pytest.raises(ValidationFailed):
        await manager.members.update_membership_role(owner, cid.id, Role.SERVICE)
    with pytest.raises(NotFound):
        await manager.members.update_membership_role(owner, new_id(), Role.MEMBER)
    member = await sign_in(manager, "cid@example.test", org.id)
    with pytest.raises(NotAuthorized):
        await manager.members.update_membership_role(member, bob.id, Role.VIEWER)


async def test_removing_a_member_soft_deletes_the_user_and_ends_access(
    manager: TenancyManagerInterface, storage: TenancyStorageMemoryImpl
) -> None:
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    owner = await sign_in(manager, "ann@example.test", org.id)
    bob = await add_member(storage, org.id, "bob@example.test", Role.ADMIN)
    cid = await add_member(storage, org.id, "cid@example.test", Role.MEMBER)
    admin = await sign_in(manager, "bob@example.test", org.id)
    cid_login = await manager.sign_in.dev_sign_in(request(), "cid@example.test")
    cid_session = await manager.sign_in.exchange_login(
        await manager.authenticate_login(request(), cid_login.token), org.id
    )
    member = await manager.authenticate(request(), cid_session.token)

    with pytest.raises(NotAuthorized):
        await manager.members.remove_member(admin, owner.user_id)
    with pytest.raises(ValidationFailed):
        await manager.members.remove_member(admin, bob.id)
    with pytest.raises(NotAuthorized):
        await manager.members.remove_member(member, bob.id)

    removed = await manager.members.remove_member(admin, cid.id)
    assert removed.deleted_at is not None and removed.deleted_by == admin.user_id
    assert removed.updated_at == removed.deleted_at
    assert [u.id for u in (await manager.members.get_users(owner, None, limit=10)).items] == sorted(
        [owner.user_id, bob.id]
    )
    with pytest.raises(CredentialExpired):  # revoked with the member
        await manager.authenticate(request(), cid_session.token)
    # Removed from the team, Cid keeps the one place nobody removes him from.
    left = (await manager.sign_in.dev_sign_in(request(), "cid@example.test")).memberships
    assert [m.org.kind for m in left] == [OrgKind.PERSONAL]
    with pytest.raises(NotFound):
        await manager.members.remove_member(admin, cid.id)
    # The membership ended with the member: no list shows it, no role change reaches it.
    assert [
        m.user_id for m in (await manager.members.get_memberships(owner, None, limit=10)).items
    ] == sorted([owner.user_id, bob.id])
    with pytest.raises(NotFound):
        await manager.members.update_membership_role(owner, cid.id, Role.VIEWER)
    ended = await storage.read_membership_for_user(org.id, cid.id)
    assert ended is None


async def test_removing_a_member_revokes_their_credentials_and_announces_each(
    storage: TenancyStorageMemoryImpl, infra: InfraLocalImpl, outbox: OutboxStorageMemoryImpl
) -> None:
    relay = SpyRelay(OutboxRelayImpl(outbox, ChangeEventStorageMemoryImpl(), infra.get_topics()))
    manager = build_tenancy(
        storage,
        relay,
        infra.get_cache(CacheScope.REALTIME_TICKET),
        TenancyOptions(dev_sign_in=True),
        identity_provider=IdentityProviderAbsentImpl(),
    )
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    owner = await sign_in(manager, "ann@example.test", org.id)
    bob = await add_member(storage, org.id, "bob@example.test", Role.MEMBER)
    bobs = await sign_in(manager, "bob@example.test", org.id)
    other = await sign_in(manager, "bob@example.test", org.id)
    key = await manager.credentials.create_api_key(bobs, "ci", Role.MEMBER)
    assert key.api_key.id in [
        k.id for k in (await manager.credentials.get_api_keys(owner, None, limit=10)).items
    ]

    await manager.members.remove_member(owner, bob.id)
    # No key of theirs stays listed for a manager, and no session of theirs is live.
    assert [
        k.user_id for k in (await manager.credentials.get_api_keys(owner, None, limit=10)).items
    ] == []
    assert await storage.read_auth_sessions(org.id, bob.id, utcnow(), 10) == []
    stored = await storage.read_api_key(org.id, key.api_key.id)
    assert stored is not None and stored.deleted_at is not None
    assert stored.deleted_by == owner.user_id
    # The removal is announced first, so their sockets close as a membership
    # that ended; then each revocation, the way a revocation is.
    kinds = [(r.kind, r.target_id) for _, r in relay.rows if r.actor_id == owner.user_id]
    assert kinds[0] == ("tenancy.user.deleted", bob.id)
    assert sorted(kinds[1:]) == sorted(
        [
            ("tenancy.auth_session.revoked", bobs.security.credential_id),
            ("tenancy.auth_session.revoked", other.security.credential_id),
            ("tenancy.api_key.deleted", key.api_key.id),
        ]
    )
    assert await claim_all(outbox) == []


async def test_no_event_about_a_user_carries_who_they_are(
    storage: TenancyStorageMemoryImpl, infra: InfraLocalImpl, outbox: OutboxStorageMemoryImpl
) -> None:
    """The stream outlives a removed member; the purge that erases a person
    reaches the user row and not the stream, so the stream never holds the
    email or the name."""
    relay = SpyRelay(OutboxRelayImpl(outbox, ChangeEventStorageMemoryImpl(), infra.get_topics()))
    manager = build_tenancy(
        storage,
        relay,
        infra.get_cache(CacheScope.REALTIME_TICKET),
        TenancyOptions(dev_sign_in=True),
        identity_provider=IdentityProviderAbsentImpl(),
    )
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    owner = await sign_in(manager, "ann@example.test", org.id)
    _, bob, _ = await manager.add_member(request(), "ajax", "bob@example.test", "Bob", Role.MEMBER)
    await manager.members.remove_member(owner, bob.id)
    about_users = [r for _, r in relay.rows if r.kind.startswith("tenancy.user.")]
    assert [r.kind for r in about_users] == ["tenancy.user.created", "tenancy.user.deleted"]
    for row in about_users:
        assert not PERSONAL_FIELDS & set(row.payload), row.kind
        assert row.payload == {"identity_id": str(bob.identity_id)}, "ids only"


class DownOnRemoveStorage(TenancyStorageMemoryImpl):
    """The writes that end a member fail while `down`: the twin of a database
    that went away between two statements."""

    down = True

    async def write_user(
        self, org_id: UUID, user: User, outbox_rows: tuple[OutboxRow, ...] = ()
    ) -> None:
        if self.down and user.deleted_at is not None:
            raise RuntimeError("storage is down")
        await super().write_user(org_id, user, outbox_rows)

    async def remove_member(
        self,
        org_id: UUID,
        user: User,
        membership: Membership,
        outbox_rows: tuple[OutboxRow, ...],
        revocation_row: Callable[[str, UUID], OutboxRow],
    ) -> tuple[OutboxRow, ...]:
        if self.down:
            raise RuntimeError("storage is down")
        return await super().remove_member(org_id, user, membership, outbox_rows, revocation_row)


async def test_a_removal_that_fails_leaves_the_member_whole(
    infra: InfraLocalImpl, outbox: OutboxStorageMemoryImpl
) -> None:
    # The user, the membership, and their credentials go in one write: a
    # failure leaves all of them, so the member is still listed, still a
    # member, still signed in, and the next remove finishes the job.
    storage = DownOnRemoveStorage(outbox)
    manager = make_manager(storage, infra, outbox=outbox)
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    owner = await sign_in(manager, "ann@example.test", org.id)
    cid = await add_member(storage, org.id, "cid@example.test", Role.MEMBER)
    cids = await sign_in(manager, "cid@example.test", org.id)
    with pytest.raises(RuntimeError):
        await manager.members.remove_member(owner, cid.id)
    session = await storage.read_auth_session(org.id, cids.credential_id)
    assert session is not None and session.revoked_at is None, "still signed in"
    assert cid.id in [u.id for u in (await manager.members.get_users(owner, None, limit=10)).items]
    assert cid.id in [
        m.user_id for m in (await manager.members.get_memberships(owner, None, limit=10)).items
    ]
    _, _, created = await manager.add_member(
        request(), "ajax", "cid@example.test", "Cid", Role.MEMBER
    )
    assert not created, "still a member"
    storage.down = False
    removed = await manager.members.remove_member(owner, cid.id)
    assert removed.deleted_at is not None
    assert cid.id not in [
        u.id for u in (await manager.members.get_users(owner, None, limit=10)).items
    ]
    assert await storage.read_membership_for_user(org.id, cid.id) is None


async def test_a_membership_needs_a_live_user_to_be_read_or_changed(
    manager: TenancyManagerInterface, storage: TenancyStorageMemoryImpl
) -> None:
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    owner = await sign_in(manager, "ann@example.test", org.id)
    cid = await add_member(storage, org.id, "cid@example.test", Role.MEMBER)
    # The user row goes while the membership row stays live: the membership is
    # still not one to list or change.
    now = utcnow()
    await storage.write_user(
        org.id, cid.model_copy(update={"deleted_at": now, "deleted_by": owner.user_id})
    )
    with pytest.raises(NotFound):
        await manager.members.update_membership_role(owner, cid.id, Role.VIEWER)
    with pytest.raises(NotFound):
        await manager.members.remove_member(owner, cid.id)


async def test_users_update_their_own_display_name(
    manager: TenancyManagerInterface, storage: TenancyStorageMemoryImpl
) -> None:
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    owner = await sign_in(manager, "ann@example.test", org.id)
    await add_member(storage, org.id, "cid@example.test", Role.VIEWER)
    viewer = await sign_in(manager, "cid@example.test", org.id)

    # The context carries the user's id; the entity is loaded by the manager.
    renamed = await manager.members.rename_user(viewer, viewer.user_id, "Cid R.")
    assert renamed.display_name == "Cid R." and renamed.updated_at > renamed.created_at
    assert renamed.updated_by == viewer.user_id
    assert await manager.members.get_user(viewer, viewer.user_id) == renamed

    with pytest.raises(NotAuthorized):
        await manager.members.rename_user(viewer, owner.user_id, "Nope")
    with pytest.raises(ValidationFailed):
        await manager.members.rename_user(viewer, viewer.user_id, "  ")
    by_owner = await manager.members.rename_user(owner, viewer.user_id, "Cid")
    assert by_owner.display_name == "Cid" and by_owner.email == "cid@example.test"


async def test_every_member_is_reachable_a_page_at_a_time(
    manager: TenancyManagerInterface, storage: TenancyStorageMemoryImpl
) -> None:
    """The member list pages the same way, so every member is reachable,
    not only whoever the first page happened to hold."""
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    for index in range(4):
        await add_member(storage, org.id, f"member-{index}@example.test", Role.MEMBER)
    owner = await sign_in(manager, "ann@example.test", org.id)
    every = sorted(u.id for u in (await manager.members.get_users(owner, None, limit=50)).items)
    assert len(every) == 5
    paged: list[UUID] = []
    after: UUID | None = None
    while True:
        page = await manager.members.get_users(owner, after, limit=2)
        paged += [u.id for u in page.items]
        if not page.has_more:
            break
        after = page.items[-1].id
    assert paged == every


async def test_memberships_pair_with_members_page_for_page(
    manager: TenancyManagerInterface, storage: TenancyStorageMemoryImpl
) -> None:
    """A page of memberships read with the cursor and limit of a page of users
    carries exactly those members' roles, so no member of a large org is
    listed without one."""
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    for index in range(4):
        await add_member(storage, org.id, f"member-{index}@example.test", Role.MEMBER)
    owner = await sign_in(manager, "ann@example.test", org.id)
    after: UUID | None = None
    pages = 0
    while True:
        users = await manager.members.get_users(owner, after, limit=2)
        roles = await manager.members.get_memberships(owner, after, limit=2)
        assert [m.user_id for m in roles.items] == [u.id for u in users.items]
        assert roles.has_more == users.has_more
        pages += 1
        if not users.has_more:
            break
        after = users.items[-1].id
    assert pages == 3


# A personal org stays its person's.


async def test_the_person_of_a_personal_org_is_never_removed_or_demoted(
    manager: TenancyManagerInterface, storage: TenancyStorageMemoryImpl
) -> None:
    dee = await signed_up(manager, "dee@example.test", "Dee")
    # Anyone may be in a personal org; nothing refuses a second person.
    other = await add_member(storage, dee.org_id, "eve@example.test", Role.OWNER)
    eve = await sign_in(manager, "eve@example.test", dee.org_id)
    with pytest.raises(PersonalOrgFixed) as refused:
        await manager.members.remove_member(eve, dee.user_id)
    assert refused.value.http_status == 409
    with pytest.raises(PersonalOrgFixed):
        await manager.members.update_membership_role(eve, dee.user_id, Role.VIEWER)
    # Another member of it is an ordinary member.
    changed = await manager.members.update_membership_role(dee, other.id, Role.MEMBER)
    assert changed.role is Role.MEMBER
    await manager.members.remove_member(dee, other.id)
    # And the person cannot leave it either: nobody removes themselves.
    with pytest.raises(ValidationFailed):
        await manager.members.remove_member(dee, dee.user_id)
