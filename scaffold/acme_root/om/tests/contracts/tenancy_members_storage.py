"""The tenancy storage cases of the methods the members delegate calls:
the users and the memberships of a tenant, a member's create and removal,
and the invitations. The fixtures and the builders it shares are in
`contracts/tenancy_storage.py`, and the cases named in
`CROSS_TENANT_CASES` are the tenant fence's evidence, as there."""

from datetime import timedelta
from uuid import UUID, uuid4

import pytest

from acme.om.base import new_id, utcnow
from acme.om.context import Role
from acme.om.exceptions import NotFound, RowDeleted, TenantMismatch, UniqueKeyTaken
from acme.om.outbox.storage import OutboxStorageInterface
from acme.om.tenancy.storage import TenancyStorageInterface
from acme.om.tenancy.types.invitation import InvitationState
from acme.om.tenancy.types.user import User

from contracts.factories import (
    make_api_key,
    make_auth_session,
    make_identity,
    make_invitation,
    make_membership,
    make_org,
    make_user,
)
from contracts.outbox_storage import claim_all
from contracts.tenancy_storage import TenancyStorageFixtures, drained, make_user_row, revocations_by

INTERFACE = TenancyStorageInterface
"""The storage interface whose cross-tenant cases this suite holds."""

CROSS_TENANT_CASES: frozenset[str] = frozenset(
    {
        "count_members",
        "create_member",
        "create_org_with_owner",
        "read_invitation",
        "read_invitation_by_provider_id",
        "read_invitations",
        "read_membership_for_user",
        "read_memberships",
        "read_pending_invitation",
        "read_user",
        "read_users",
        "remove_member",
        "write_invitation",
        "write_membership",
        "write_user",
    }
)
"""The methods of `TenancyStorageInterface` whose case presenting another
tenant's identifier is in this module. `test_storage_exceptions.py` adds
up the sets of every module that names the interface and holds them to
every method that takes a tenant, so a new method arrives with its case."""


class TenancyMembersStorageContract(TenancyStorageFixtures):
    async def test_a_write_lands_every_row_that_announces_it(
        self, storage: TenancyStorageInterface, outbox: OutboxStorageInterface
    ) -> None:
        """A write on a core entity takes the rows that announce it as a tuple,
        so they land in one statement with it. An entity change is one row; a
        write that also starts work carries a second of kind `work.<kind>`,
        because the queue is a role of its own and no statement reaches both."""
        org = make_org()
        await storage.write_org(org.id, org)
        user = make_user(make_identity().id)
        change = make_user_row(org.id, user)
        asked = change.model_copy(update={"id": new_id(), "kind": "work.noop"})
        await storage.create_member(org.id, user, make_membership(user.id), (change, asked))
        landed = [row.id for row in await claim_all(outbox) if row.target_id == user.id]
        assert sorted(landed) == sorted([change.id, asked.id])

    async def test_reads_are_tenant_scoped(self, storage: TenancyStorageInterface) -> None:
        org_a, org_b = make_org("A"), make_org("B")
        identity = make_identity()
        user = make_user(identity.id)
        await storage.write_user(org_a.id, user)
        assert await storage.read_user(org_a.id, user.id) == user
        assert await storage.read_user(org_b.id, user.id) is None
        assert await storage.read_users(org_b.id, None, limit=10) == []

    async def test_write_refuses_another_tenant(self, storage: TenancyStorageInterface) -> None:
        org_a, org_b = make_org("A"), make_org("B")
        user = make_user(make_identity().id)
        await storage.write_user(org_a.id, user)
        with pytest.raises(TenantMismatch):
            await storage.write_user(org_b.id, user.model_copy(update={"display_name": "Moved"}))
        assert (await storage.read_user(org_a.id, user.id)) == user

    async def test_the_membership_reads_and_writes_are_tenant_scoped(
        self, storage: TenancyStorageInterface
    ) -> None:
        org_a, org_b = make_org("A"), make_org("B")
        membership = make_membership(new_id())
        await storage.write_membership(org_a.id, membership)
        assert await storage.read_memberships(org_b.id, limit=10) == []
        assert await storage.read_membership_for_user(org_b.id, membership.user_id) is None
        with pytest.raises(TenantMismatch):
            await storage.write_membership(
                org_b.id, membership.model_copy(update={"role": Role.OWNER})
            )
        assert await storage.read_memberships(org_a.id, limit=10) == [membership]

    async def test_the_member_count_is_the_tenants_live_memberships(
        self, storage: TenancyStorageInterface
    ) -> None:
        """Live memberships of this tenant, an ended one not among them,
        another tenant's never."""
        org_a, org_b = make_org("A"), make_org("B")
        kept, ended = make_membership(new_id()), make_membership(new_id())
        await storage.write_membership(org_a.id, kept)
        await storage.write_membership(org_a.id, ended)
        await storage.write_membership(
            org_a.id, ended.model_copy(update={"deleted_at": utcnow(), "deleted_by": new_id()})
        )
        assert await storage.count_members(org_a.id) == 1
        assert await storage.count_members(org_b.id) == 0

    async def test_the_owner_count_is_the_tenants_live_owners(
        self, storage: TenancyStorageInterface
    ) -> None:
        """The owners a leaving owner would leave behind: live owner
        memberships of this tenant, and no other tenant's."""
        org_a, org_b = make_org("A"), make_org("B")
        owner, member = make_membership(new_id(), Role.OWNER), make_membership(new_id())
        ended = make_membership(new_id(), Role.OWNER)
        for membership in (owner, member, ended):
            await storage.write_membership(org_a.id, membership)
        await storage.write_membership(
            org_a.id, ended.model_copy(update={"deleted_at": utcnow(), "deleted_by": new_id()})
        )
        await storage.write_membership(org_b.id, make_membership(new_id(), Role.OWNER))
        assert await storage.count_members(org_a.id, Role.OWNER) == 1
        assert await storage.count_members(org_a.id, Role.MEMBER) == 1
        assert await storage.count_members(org_a.id) == 2

    async def test_a_member_is_never_created_on_another_tenants_row(
        self, storage: TenancyStorageInterface
    ) -> None:
        """Both creates land whole or not at all, and an id another tenant
        holds is a taken key: the rows beside it land nothing either."""
        org, other = make_org("A"), make_org("B")
        user = make_user(make_identity().id)
        membership = make_membership(user.id)
        await storage.create_member(org.id, user, membership, (make_user_row(org.id, user),))
        with pytest.raises(UniqueKeyTaken):
            await storage.create_member(
                other.id, user, make_membership(user.id), (make_user_row(other.id, user),)
            )
        assert await storage.read_user(other.id, user.id) is None
        assert await storage.read_memberships(other.id, limit=10) == []
        with pytest.raises(UniqueKeyTaken):
            await storage.create_org_with_owner(other.id, other, user, make_membership(user.id))
        assert await storage.read_org(other.id) is None
        assert await storage.read_user(org.id, user.id) == user
        assert await storage.read_membership_for_user(org.id, user.id) == membership

    async def test_update_by_copy_and_write(self, storage: TenancyStorageInterface) -> None:
        org = make_org()
        user = make_user(make_identity().id)
        await storage.write_user(org.id, user)
        renamed = user.model_copy(update={"display_name": "Renamed", "updated_at": utcnow()})
        await storage.write_user(org.id, renamed)
        assert await storage.read_user(org.id, user.id) == renamed

    async def test_lists_sort_by_uuid_value_and_clamp(
        self, storage: TenancyStorageInterface
    ) -> None:
        org = make_org()
        users = [make_user(make_identity().id) for _ in range(3)]  # one live user per identity
        for user in reversed(users):
            await storage.write_user(org.id, user)
        listed = await storage.read_users(org.id, None, limit=10)
        assert listed == sorted(users, key=lambda u: u.id)
        assert len(await storage.read_users(org.id, None, limit=2)) == 2

    async def test_the_user_list_pages_after_a_cursor(
        self, storage: TenancyStorageInterface
    ) -> None:
        """A cursor is the id the previous page ended on, and the next page
        starts strictly after it, so every member is reachable however small
        the page is."""
        org = make_org()
        users = sorted((make_user(make_identity().id) for _ in range(5)), key=lambda u: u.id)
        for user in users:
            await storage.write_user(org.id, user)
        paged: list[User] = []
        after: UUID | None = None
        while page := await storage.read_users(org.id, after, limit=2):
            paged += page
            after = page[-1].id
        assert paged == users
        assert await storage.read_users(org.id, users[-1].id, limit=10) == []

    async def test_soft_deleted_users_are_hidden_from_lists(
        self, storage: TenancyStorageInterface
    ) -> None:
        org = make_org()
        user = make_user(make_identity().id)
        await storage.write_user(org.id, user)
        gone = user.model_copy(update={"deleted_at": utcnow(), "deleted_by": user.id})
        await storage.write_user(org.id, gone)
        assert await storage.read_users(org.id, None, limit=10) == []
        assert await storage.read_user(org.id, user.id) == gone

    async def test_a_write_never_brings_a_deleted_row_back(
        self, storage: TenancyStorageInterface
    ) -> None:
        """Every update is a read, a copy, and a write of the whole entity, so
        a delete that commits between the read and the write would be undone by
        a copy still carrying `deleted_at = None`, leaving a live user with no
        live membership: listed, unable to sign in, and past every sweep. There
        is no restore in this domain, so the write is refused instead."""
        org = make_org()
        user = make_user(make_identity().id)
        await storage.write_user(org.id, user)
        held = await storage.read_user(org.id, user.id)
        assert held is not None
        gone = user.model_copy(update={"deleted_at": utcnow(), "deleted_by": user.id})
        await storage.write_user(org.id, gone)
        renamed = held.model_copy(update={"display_name": "Renamed", "updated_at": utcnow()})
        with pytest.raises(RowDeleted):
            await storage.write_user(org.id, renamed)
        assert await storage.read_user(org.id, user.id) == gone
        assert await storage.read_users(org.id, None, limit=10) == []

    async def test_one_live_user_per_identity_in_a_tenant(
        self, storage: TenancyStorageInterface
    ) -> None:
        org, other_org = make_org(), make_org("Other")
        identity = make_identity()
        user = make_user(identity.id)
        await storage.write_user(org.id, user)
        with pytest.raises(UniqueKeyTaken):
            await storage.write_user(org.id, make_user(identity.id))
        # The same identity in another tenant, and again here once the first is gone.
        await storage.write_user(other_org.id, make_user(identity.id))
        gone = user.model_copy(update={"deleted_at": utcnow(), "deleted_by": user.id})
        await storage.write_user(org.id, gone)
        await storage.write_user(org.id, make_user(identity.id))

    # Each unique key the schema declares on these rows has a case here, so
    # the memory impl refuses what the engine refuses: the write raises
    # UniqueKeyTaken and the row that held the key is unchanged. An update by
    # copy of the row that holds the key passes.

    async def test_one_membership_per_user_in_a_tenant(
        self, storage: TenancyStorageInterface
    ) -> None:
        org, other_org = make_org(), make_org("Other")
        membership = make_membership(new_id())
        await storage.write_membership(org.id, membership)
        with pytest.raises(UniqueKeyTaken):
            await storage.write_membership(org.id, make_membership(membership.user_id))
        assert await storage.read_memberships(org.id, limit=10) == [membership]
        # The same user id in another tenant is another key.
        await storage.write_membership(other_org.id, make_membership(membership.user_id))
        promoted = membership.model_copy(update={"role": Role.ADMIN})
        await storage.write_membership(org.id, promoted)
        assert await storage.read_membership_for_user(org.id, membership.user_id) == promoted

    # A unique key on a soft-deletable table is unique among the living: a
    # deleted row frees its key, the same value is created again, and a second
    # live row is still refused.

    async def test_an_ended_membership_frees_its_user(
        self, storage: TenancyStorageInterface
    ) -> None:
        org = make_org()
        membership = make_membership(new_id())
        await storage.write_membership(org.id, membership)
        ended = membership.model_copy(update={"deleted_at": utcnow(), "deleted_by": new_id()})
        await storage.write_membership(org.id, ended)
        assert await storage.read_membership_for_user(org.id, membership.user_id) is None
        again = make_membership(membership.user_id)
        await storage.write_membership(org.id, again)
        assert await storage.read_membership_for_user(org.id, membership.user_id) == again
        with pytest.raises(UniqueKeyTaken):
            await storage.write_membership(org.id, make_membership(membership.user_id))
        assert await storage.read_memberships(org.id, limit=10) == [again]

    async def test_create_member_lands_whole_or_not_at_all(
        self, storage: TenancyStorageInterface
    ) -> None:
        org = make_org()
        identity = make_identity()
        bob = make_user(identity.id)
        await storage.create_member(
            org.id, bob, make_membership(bob.id), (make_user_row(org.id, bob),)
        )
        assert await storage.read_user(org.id, bob.id) == bob
        assert (await storage.read_membership_for_user(org.id, bob.id)) is not None
        # The same identity added again meanwhile: the user key refuses it and
        # the membership does not land either.
        again = make_user(identity.id)
        with pytest.raises(UniqueKeyTaken):
            await storage.create_member(
                org.id, again, make_membership(again.id), (make_user_row(org.id, again),)
            )
        assert await storage.read_user(org.id, again.id) is None
        assert await storage.read_membership_for_user(org.id, again.id) is None
        # A membership the user already holds: the user does not land either.
        cid = make_user(make_identity().id)
        with pytest.raises(UniqueKeyTaken):
            await storage.create_member(
                org.id, cid, make_membership(bob.id), (make_user_row(org.id, cid),)
            )
        assert await storage.read_user(org.id, cid.id) is None
        assert len(await storage.read_memberships(org.id, limit=10)) == 1
        # A new identity lands with the member, or not at all.
        newcomer = make_identity()
        dan = make_user(newcomer.id)
        with pytest.raises(UniqueKeyTaken):
            await storage.create_member(
                org.id, dan, make_membership(bob.id), (make_user_row(org.id, dan),), newcomer
            )
        assert await storage.read_identity(newcomer.id) is None
        await storage.create_member(
            org.id, dan, make_membership(dan.id), (make_user_row(org.id, dan),), newcomer
        )
        assert await storage.read_identity(newcomer.id) == newcomer
        assert await storage.read_user(org.id, dan.id) == dan

    async def test_remove_member_lands_whole_or_not_at_all(
        self, storage: TenancyStorageInterface
    ) -> None:
        org, other = make_org(), make_org("Other")
        bob = make_user(make_identity().id)
        membership = make_membership(bob.id)
        await storage.create_member(org.id, bob, membership, (make_user_row(org.id, bob),))
        gone = utcnow()
        removed = bob.model_copy(update={"deleted_at": gone, "deleted_by": bob.id})
        ended = membership.model_copy(update={"deleted_at": gone, "deleted_by": bob.id})
        # A membership that is not there, or a user of another tenant: nothing lands.
        revoke = revocations_by(bob.id, org.id)
        with pytest.raises(NotFound):
            await storage.remove_member(
                org.id, removed, make_membership(bob.id), (make_user_row(org.id, bob),), revoke
            )
        # The two impls name the refusal differently (see
        # docs/runbooks/tenant-isolation.md); both refuse and both land nothing.
        # A row of another tenant is refused the way a row that is not there
        # is, by both impls: the answer says nothing about whether it exists
        # under someone else.
        with pytest.raises(NotFound):
            await storage.remove_member(
                other.id, removed, ended, (make_user_row(other.id, bob),), revoke
            )
        assert await storage.read_user(org.id, bob.id) == bob
        assert await storage.read_membership_for_user(org.id, bob.id) == membership
        assert await storage.read_users(other.id, None, limit=10) == []
        assert await storage.read_memberships(other.id, limit=10) == []
        await storage.remove_member(org.id, removed, ended, (make_user_row(org.id, bob),), revoke)
        assert await storage.read_user(org.id, bob.id) == removed
        assert await storage.read_membership_for_user(org.id, bob.id) is None
        assert await storage.read_users(org.id, None, limit=10) == []

    async def test_remove_member_revokes_every_credential_of_theirs_in_the_same_commit(
        self, storage: TenancyStorageInterface, outbox: OutboxStorageInterface
    ) -> None:
        """The member, their membership, their live sessions, and their keys
        end together, each credential with the row that announces it; another
        member's credentials, and the same person's in another tenant, stay."""
        org, other = make_org(), make_org("Other")
        identity = make_identity()
        bob, cid = make_user(identity.id), make_user(make_identity().id)
        bob_elsewhere = make_user(identity.id)
        membership = make_membership(bob.id)
        await storage.create_member(org.id, bob, membership, (make_user_row(org.id, bob),))
        await storage.create_member(org.id, cid, make_membership(cid.id), ())
        await storage.create_member(other.id, bob_elsewhere, make_membership(bob_elsewhere.id), ())
        live = make_auth_session(identity.id, bob.id, uuid4().hex)
        dead = make_auth_session(identity.id, bob.id, uuid4().hex)
        await storage.write_auth_session(org.id, live)
        await storage.write_auth_session(org.id, dead.model_copy(update={"revoked_at": utcnow()}))
        key = make_api_key(bob.id, uuid4().hex)
        await storage.write_api_key(org.id, key)
        cids = make_auth_session(cid.id, cid.id, uuid4().hex)
        await storage.write_auth_session(org.id, cids)
        elsewhere = make_auth_session(identity.id, bob_elsewhere.id, uuid4().hex)
        await storage.write_auth_session(other.id, elsewhere)

        gone = utcnow()
        removed = bob.model_copy(update={"deleted_at": gone, "deleted_by": cid.id})
        ended = membership.model_copy(update={"deleted_at": gone, "deleted_by": cid.id})
        rows = await storage.remove_member(
            org.id, removed, ended, (make_user_row(org.id, bob),), revocations_by(cid.id, org.id)
        )
        assert sorted((r.kind, r.target_id) for r in rows) == sorted(
            [("tenancy.auth_session.revoked", live.id), ("tenancy.api_key.deleted", key.id)]
        )
        revoked = await storage.read_auth_session(org.id, live.id)
        assert revoked is not None and revoked.revoked_at == gone and revoked.updated_by == cid.id
        deleted = await storage.read_api_key(org.id, key.id)
        assert deleted is not None and (deleted.deleted_at, deleted.deleted_by) == (gone, cid.id)
        assert await storage.read_auth_session(org.id, cids.id) == cids
        assert await storage.read_auth_session(other.id, elsewhere.id) == elsewhere
        ours = {r.id for r in rows}
        landed = {(r.org_id, r.kind, r.target_id) for r in await claim_all(outbox) if r.id in ours}
        assert landed == {
            (org.id, "tenancy.auth_session.revoked", live.id),
            (org.id, "tenancy.api_key.deleted", key.id),
        }

    async def test_users_by_identity_span_tenants(self, storage: TenancyStorageInterface) -> None:
        identity = make_identity()
        org_a, org_b = make_org("A"), make_org("B")
        user_a, user_b = make_user(identity.id), make_user(identity.id)
        await storage.write_user(org_a.id, user_a)
        await storage.write_user(org_b.id, user_b)
        found = await storage.read_users_by_identity(identity.id, limit=10)
        expected = sorted([(org_a.id, user_a), (org_b.id, user_b)], key=lambda pair: pair[1].id)
        assert found == expected, "by user id"
        # The bound is in the statement, and both impls cut at the same row.
        assert await storage.read_users_by_identity(identity.id, limit=1) == expected[:1]

    async def test_membership_for_user(self, storage: TenancyStorageInterface) -> None:
        org = make_org()
        membership = make_membership(new_id())
        await storage.write_membership(org.id, membership)
        assert await storage.read_membership_for_user(org.id, membership.user_id) == membership
        assert await storage.read_membership_for_user(org.id, new_id()) is None
        assert await storage.read_memberships(org.id, limit=5) == [membership]

    async def test_the_membership_list_pages_by_user_id(
        self, storage: TenancyStorageInterface
    ) -> None:
        """Memberships are listed by user id, not by their own id, so a page of
        them covers the members a page of users ending on the same id does.
        The user ids here are minted newest first, against the membership ids."""
        org = make_org()
        user_ids = [new_id() for _ in range(5)][::-1]
        memberships = [make_membership(user_id) for user_id in user_ids]
        for membership in memberships:
            await storage.write_membership(org.id, membership)
        by_user = sorted(memberships, key=lambda m: m.user_id)
        paged = []
        after: UUID | None = None
        while page := await storage.read_memberships(org.id, 2, after):
            paged += page
            after = page[-1].user_id
        assert paged == by_user
        assert await storage.read_memberships(org.id, 10, by_user[-1].user_id) == []

    async def test_an_ended_membership_is_hidden_from_reads_and_purged_past_retention(
        self, storage: TenancyStorageInterface
    ) -> None:
        org = make_org()
        cut = await drained(storage)
        live, ended = make_membership(new_id()), make_membership(new_id())
        await storage.write_membership(org.id, live)
        await storage.write_membership(org.id, ended)
        await storage.write_membership(
            org.id,
            ended.model_copy(update={"deleted_at": cut - timedelta(days=1), "deleted_by": live.id}),
        )
        assert await storage.read_memberships(org.id, limit=10) == [live]
        assert await storage.read_membership_for_user(org.id, ended.user_id) is None
        assert await storage.read_membership_for_user(org.id, live.user_id) == live
        assert await storage.purge_deleted(cut, cut, 10) == 1  # the ended membership
        assert await storage.read_memberships(org.id, limit=10) == [live]
        assert await storage.purge_deleted(cut, cut, 10) == 0

    # Invitations: a tenant's rows.

    async def test_an_invitation_round_trips_and_is_found_three_ways(
        self, storage: TenancyStorageInterface
    ) -> None:
        org = make_org()
        await storage.write_org(org.id, org)
        invitation = make_invitation("ann@example.test")
        await storage.write_invitation(org.id, invitation)
        assert await storage.read_invitation(org.id, invitation.id) == invitation
        found = await storage.read_invitation_by_provider_id(
            org.id, invitation.provider_invitation_id
        )
        assert found == invitation
        assert await storage.read_pending_invitation(org.id, "ann@example.test") == invitation
        assert await storage.read_pending_invitation(org.id, "bob@example.test") is None
        accepted = invitation.model_copy(update={"state": InvitationState.ACCEPTED})
        await storage.write_invitation(org.id, accepted)
        assert await storage.read_pending_invitation(org.id, "ann@example.test") is None
        assert await storage.read_invitation(org.id, invitation.id) == accepted

    async def test_the_invitation_reads_and_writes_are_tenant_scoped(
        self, storage: TenancyStorageInterface
    ) -> None:
        org, other = make_org("A"), make_org("B")
        invitation = make_invitation("ann@example.test")
        await storage.write_invitation(org.id, invitation)
        assert await storage.read_invitation(other.id, invitation.id) is None
        assert (
            await storage.read_invitation_by_provider_id(
                other.id, invitation.provider_invitation_id
            )
            is None
        )
        assert await storage.read_pending_invitation(other.id, "ann@example.test") is None
        assert await storage.read_invitations(other.id, None, limit=10) == []
        with pytest.raises(TenantMismatch):
            await storage.write_invitation(
                other.id, invitation.model_copy(update={"state": InvitationState.REVOKED})
            )
        assert await storage.read_invitation(org.id, invitation.id) == invitation

    async def test_pending_invitations_list_newest_first_after_a_cursor(
        self, storage: TenancyStorageInterface
    ) -> None:
        org = make_org()
        made = [make_invitation(f"p{n}@example.test") for n in range(4)]
        for invitation in made:
            await storage.write_invitation(org.id, invitation)
        closed = made[1].model_copy(update={"state": InvitationState.REVOKED})
        await storage.write_invitation(org.id, closed)
        pending = [made[3], made[2], made[0]]
        assert await storage.read_invitations(org.id, None, limit=10) == pending
        first = await storage.read_invitations(org.id, None, limit=2)
        assert first == pending[:2]
        assert await storage.read_invitations(org.id, first[-1].id, limit=2) == pending[2:]

    async def test_an_invitation_keeps_its_two_unique_keys(
        self, storage: TenancyStorageInterface
    ) -> None:
        org, other = make_org("A"), make_org("B")
        first = make_invitation("ann@example.test")
        await storage.write_invitation(org.id, first)
        # One pending invitation per address in a tenant.
        with pytest.raises(UniqueKeyTaken):
            await storage.write_invitation(org.id, make_invitation("ann@example.test"))
        # The address is free in another tenant, and once the first closes.
        await storage.write_invitation(other.id, make_invitation("ann@example.test"))
        await storage.write_invitation(
            org.id, first.model_copy(update={"state": InvitationState.REVOKED})
        )
        await storage.write_invitation(org.id, make_invitation("ann@example.test"))
        # The provider's id names one invitation, across every tenant.
        with pytest.raises(UniqueKeyTaken):
            await storage.write_invitation(
                other.id,
                make_invitation(
                    "cat@example.test", provider_invitation_id=first.provider_invitation_id
                ),
            )

    async def test_a_member_lands_with_the_invitation_they_accept_or_not_at_all(
        self, storage: TenancyStorageInterface
    ) -> None:
        org, other = make_org(), make_org("Other")
        await storage.write_org(org.id, org)
        invitation = make_invitation("dan@example.test", Role.ADMIN)
        await storage.write_invitation(org.id, invitation)
        newcomer = make_identity("dan@example.test")
        dan = make_user(newcomer.id, newcomer.email)
        accepted = invitation.model_copy(
            update={"state": InvitationState.ACCEPTED, "accepted_user_id": dan.id}
        )
        # Another tenant's invitation lands nothing, the member included.
        with pytest.raises(NotFound):
            await storage.create_member(
                other.id,
                dan,
                make_membership(dan.id),
                (make_user_row(other.id, dan),),
                newcomer,
                invitation=accepted,
            )
        assert await storage.read_identity(newcomer.id) is None
        assert await storage.read_user(other.id, dan.id) is None
        await storage.create_member(
            org.id,
            dan,
            make_membership(dan.id, Role.ADMIN),
            (make_user_row(org.id, dan),),
            newcomer,
            invitation=accepted,
        )
        assert await storage.read_user(org.id, dan.id) == dan
        assert await storage.read_invitation(org.id, invitation.id) == accepted
