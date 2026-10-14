"""The tenancy storage cases of the methods the org delegate calls: the
orgs and their slugs, the create of an org with its owner and of a person
with their personal org, the time zone, the close of an org, and the
deletion of a person. The fixtures and the builders it shares are in
`contracts/tenancy_storage.py`, and the cases named in
`CROSS_TENANT_CASES` are the tenant fence's evidence, as there."""

from uuid import UUID, uuid4

import pytest

from acme.om.base import EMPTY_UUID, new_id, utcnow
from acme.om.context import OperatorRole, Role
from acme.om.exceptions import Conflict, NotFound, TenantMismatch, UniqueKeyTaken
from acme.om.outbox.storage import OutboxStorageInterface
from acme.om.outbox.types.row import OutboxRow
from acme.om.tenancy.rules import email_digest
from acme.om.tenancy.storage import TenancyStorageInterface
from acme.om.tenancy.types.identity import Identity
from acme.om.tenancy.types.invitation import InvitationState
from acme.om.tenancy.types.user import User

from contracts.factories import (
    make_api_key,
    make_auth_session,
    make_identity,
    make_invitation,
    make_membership,
    make_org,
    make_personal_org,
    make_sign_in,
    make_socket_ticket,
    make_user,
)
from contracts.outbox_storage import claim_all
from contracts.racing import race
from contracts.tenancy_storage import TenancyStorageFixtures, make_user_row, revocations_by

INTERFACE = TenancyStorageInterface
"""The storage interface whose cross-tenant cases this suite holds."""

CROSS_TENANT_CASES: frozenset[str] = frozenset(
    {
        "read_org",
        "write_closed_org",
        "write_org",
    }
)
"""The methods of `TenancyStorageInterface` whose case presenting another
tenant's identifier is in this module. `test_storage_exceptions.py` adds
up the sets of every module that names the interface and holds them to
every method that takes a tenant, so a new method arrives with its case."""


class TenancyOrgStorageContract(TenancyStorageFixtures):
    async def test_org_round_trip(self, storage: TenancyStorageInterface) -> None:
        org = make_org()
        await storage.write_org(org.id, org)
        assert await storage.read_org(org.id) == org
        assert await storage.read_org_by_slug(org.slug) == org
        assert org in await storage.read_orgs(limit=1000)

    async def test_an_org_write_lands_its_outbox_row_beside_it(
        self, storage: TenancyStorageInterface
    ) -> None:
        org = make_org()
        await storage.write_org(org.id, org)
        deleted = org.model_copy(update={"deleted_at": utcnow(), "deleted_by": new_id()})
        row = OutboxRow(
            id=new_id(),
            created_at=utcnow(),
            org_id=org.id,
            kind="tenancy.org.deleted",
            target_id=org.id,
            payload={"slug": org.slug},
            actor_id=new_id(),
            request_id=new_id(),
            app="portal",
        )
        await storage.write_org(org.id, deleted, (row,))
        assert await storage.read_org(org.id) == deleted
        assert await storage.read_org_by_slug(org.slug) is None

    async def test_the_org_read_and_write_are_tenant_scoped(
        self, storage: TenancyStorageInterface
    ) -> None:
        """An org is its own tenant, so `read_org` names the id twice and a
        tenant that holds no org reads nothing. The write is the fence: an org
        row is never moved under another tenant."""
        org, other = make_org("A"), make_org("B")
        await storage.write_org(org.id, org)
        assert await storage.read_org(other.id) is None
        with pytest.raises(TenantMismatch):
            await storage.write_org(other.id, org.model_copy(update={"name": "Stolen"}))
        assert await storage.read_org(org.id) == org

    # Each unique key the schema declares on these rows has a case here, so
    # the memory impl refuses what the engine refuses: the write raises
    # UniqueKeyTaken and the row that held the key is unchanged. An update by
    # copy of the row that holds the key passes.

    async def test_org_slug_is_unique(self, storage: TenancyStorageInterface) -> None:
        org = make_org()
        await storage.write_org(org.id, org)
        other = make_org("Other").model_copy(update={"slug": org.slug})
        with pytest.raises(UniqueKeyTaken):
            await storage.write_org(other.id, other)
        assert await storage.read_org(other.id) is None
        assert await storage.read_org_by_slug(org.slug) == org
        renamed = org.model_copy(update={"name": "Renamed"})
        await storage.write_org(org.id, renamed)
        assert await storage.read_org_by_slug(org.slug) == renamed

    # A unique key on a soft-deletable table is unique among the living: a
    # deleted row frees its key, the same value is created again, and a second
    # live row is still refused.

    async def test_a_deleted_org_frees_its_slug(self, storage: TenancyStorageInterface) -> None:
        org = make_org()
        await storage.write_org(org.id, org)
        gone = org.model_copy(update={"deleted_at": utcnow(), "deleted_by": new_id()})
        await storage.write_org(org.id, gone)
        assert await storage.read_org_by_slug(org.slug) is None
        again = make_org().model_copy(update={"slug": org.slug})
        await storage.write_org(again.id, again)
        assert await storage.read_org_by_slug(org.slug) == again
        assert await storage.read_org(org.id) == gone
        other = make_org("Other").model_copy(update={"slug": org.slug})
        with pytest.raises(UniqueKeyTaken):
            await storage.write_org(other.id, other)
        assert await storage.read_org(other.id) is None

    async def test_create_org_with_owner_lands_whole_or_not_at_all(
        self, storage: TenancyStorageInterface
    ) -> None:
        org = make_org()
        owner = make_user(make_identity().id)
        await storage.create_org_with_owner(
            org.id, org, owner, make_membership(owner.id, Role.OWNER)
        )
        assert await storage.read_org(org.id) == org
        assert await storage.read_user(org.id, owner.id) == owner
        assert (await storage.read_membership_for_user(org.id, owner.id)) is not None
        # The slug taken meanwhile: no org, no user, no membership of the loser.
        other = make_org("Other").model_copy(update={"slug": org.slug})
        loser = make_user(make_identity().id)
        with pytest.raises(UniqueKeyTaken):
            await storage.create_org_with_owner(
                other.id, other, loser, make_membership(loser.id, Role.OWNER)
            )
        assert await storage.read_org(other.id) is None
        assert await storage.read_user(other.id, loser.id) is None
        assert await storage.read_memberships(other.id, limit=10) == []

    async def test_create_org_with_owner_lands_the_identity_in_the_same_commit(
        self, storage: TenancyStorageInterface
    ) -> None:
        # A new identity lands with its tenant; when the slug is taken it does
        # not land at all, and an existing identity is not promoted either.
        org, identity = make_org(), make_identity()
        owner = make_user(identity.id)
        await storage.create_org_with_owner(
            org.id, org, owner, make_membership(owner.id, Role.OWNER), identity
        )
        assert await storage.read_identity(identity.id) == identity
        other = make_org("Other").model_copy(update={"slug": org.slug})
        newcomer = make_identity()
        loser = make_user(newcomer.id)
        with pytest.raises(UniqueKeyTaken):
            await storage.create_org_with_owner(
                other.id, other, loser, make_membership(loser.id, Role.OWNER), newcomer
            )
        assert await storage.read_identity(newcomer.id) is None
        promoted = identity.model_copy(update={"operator_role": OperatorRole.WRITE})
        again = make_user(identity.id)
        with pytest.raises(UniqueKeyTaken):
            await storage.create_org_with_owner(
                other.id, other, again, make_membership(again.id, Role.OWNER), promoted
            )
        assert await storage.read_identity(identity.id) == identity
        # From a free slug the promotion lands with the tenant.
        free = make_org("Free")
        await storage.create_org_with_owner(
            free.id, free, again, make_membership(again.id, Role.OWNER), promoted
        )
        assert await storage.read_identity(identity.id) == promoted

    async def test_one_personal_org_per_person_among_the_living(
        self, storage: TenancyStorageInterface
    ) -> None:
        identity = make_identity()
        mine = make_personal_org(identity.id)
        owner = make_user(identity.id)
        await storage.create_org_with_owner(
            mine.id, mine, owner, make_membership(owner.id, Role.OWNER), identity
        )
        stored = await storage.read_org(mine.id)
        assert stored == mine and stored is not None and stored.personal
        # A second one for the same person is refused, and lands nothing.
        second = make_personal_org(identity.id)
        again = make_user(identity.id)
        with pytest.raises(UniqueKeyTaken):
            await storage.create_org_with_owner(
                second.id, second, again, make_membership(again.id, Role.OWNER)
            )
        assert await storage.read_org(second.id) is None
        assert await storage.read_user(second.id, again.id) is None
        # A team org of the same person is no personal org, and another
        # person's personal org is theirs.
        team = make_org("Team")
        member = make_user(identity.id)
        await storage.create_org_with_owner(
            team.id, team, member, make_membership(member.id, Role.OWNER)
        )
        other = make_identity()
        theirs = make_personal_org(other.id, "Eve")
        eve = make_user(other.id)
        await storage.create_org_with_owner(
            theirs.id, theirs, eve, make_membership(eve.id, Role.OWNER), other
        )
        # Unique among the living: a deleted one frees the person's key.
        gone = utcnow()
        await storage.write_org(
            mine.id, mine.model_copy(update={"deleted_at": gone, "deleted_by": owner.id})
        )
        await storage.create_org_with_owner(
            second.id, second, again, make_membership(again.id, Role.OWNER)
        )
        assert await storage.read_org(second.id) == second

    async def test_a_new_persons_personal_org_lands_with_the_create_or_not_at_all(
        self, storage: TenancyStorageInterface
    ) -> None:
        taken = make_org("Taken")
        first = make_user(make_identity().id)
        await storage.create_org_with_owner(
            taken.id, taken, first, make_membership(first.id, Role.OWNER)
        )
        # Two tenants in one commit, each under its own fence: a slug taken in
        # the second leaves nothing of the first, nor the identity.
        newcomer = make_identity()
        personal = make_personal_org(newcomer.id)
        mine = make_user(newcomer.id)
        own = (personal, mine, make_membership(mine.id, Role.OWNER))
        clash = make_org("Clash").model_copy(update={"slug": taken.slug})
        owner = make_user(newcomer.id)
        with pytest.raises(UniqueKeyTaken):
            await storage.create_org_with_owner(
                clash.id, clash, owner, make_membership(owner.id, Role.OWNER), newcomer, own
            )
        assert await storage.read_identity(newcomer.id) is None
        assert await storage.read_org(personal.id) is None
        assert await storage.read_user(personal.id, mine.id) is None
        # From a free slug both tenants land, and the identity with them.
        team = make_org("Team")
        await storage.create_org_with_owner(
            team.id, team, owner, make_membership(owner.id, Role.OWNER), newcomer, own
        )
        assert await storage.read_identity(newcomer.id) == newcomer
        assert await storage.read_org(personal.id) == personal
        assert await storage.read_org(team.id) == team
        assert await storage.read_user(personal.id, mine.id) == mine
        assert await storage.read_user(team.id, owner.id) == owner
        places = await storage.read_memberships_by_identity(newcomer.id, 10)
        assert {p.org.id for p in places} == {personal.id, team.id}
        # The same for a member: a new person joins with their personal org.
        joiner = make_identity()
        theirs = make_personal_org(joiner.id, "Eve")
        eve_home = make_user(joiner.id)
        eve = make_user(joiner.id)
        eve_own = (theirs, eve_home, make_membership(eve_home.id, Role.OWNER))
        with pytest.raises(UniqueKeyTaken):
            await storage.create_member(
                team.id,
                eve,
                make_membership(owner.id),
                (make_user_row(team.id, eve),),
                joiner,
                eve_own,
            )
        assert await storage.read_org(theirs.id) is None
        assert await storage.read_identity(joiner.id) is None
        await storage.create_member(
            team.id, eve, make_membership(eve.id), (make_user_row(team.id, eve),), joiner, eve_own
        )
        assert await storage.read_org(theirs.id) == theirs
        assert await storage.read_user(team.id, eve.id) == eve

    async def test_write_closed_org_ends_every_way_in_and_nothing_elsewhere(
        self, storage: TenancyStorageInterface, outbox: OutboxStorageInterface
    ) -> None:
        """Every member, their memberships, sessions, and keys, and every
        pending invitation of the tenant end together with the org's own
        change, each ended user and credential with the row that announces
        it. The same person in another tenant keeps everything, and so does
        that tenant. Presented under another tenant, nothing lands."""
        org, other = make_org(), make_org("Other")
        await storage.write_org(org.id, org)
        await storage.write_org(other.id, other)
        identity = make_identity()
        ann = make_user(identity.id)
        bob = make_user(make_identity().id)
        ann_elsewhere = make_user(identity.id)
        await storage.create_member(org.id, ann, make_membership(ann.id, Role.OWNER), ())
        await storage.create_member(org.id, bob, make_membership(bob.id), ())
        await storage.create_member(other.id, ann_elsewhere, make_membership(ann_elsewhere.id), ())
        anns = make_auth_session(identity.id, ann.id, uuid4().hex)
        bobs = make_auth_session(bob.id, bob.id, uuid4().hex)
        elsewhere = make_auth_session(identity.id, ann_elsewhere.id, uuid4().hex)
        await storage.write_auth_session(org.id, anns)
        await storage.write_auth_session(org.id, bobs)
        await storage.write_auth_session(other.id, elsewhere)
        key = make_api_key(bob.id, uuid4().hex)
        await storage.write_api_key(org.id, key)
        pending = make_invitation("dee@example.test")
        theirs = make_invitation("eve@example.test")
        await storage.write_invitation(org.id, pending)
        await storage.write_invitation(other.id, theirs)

        gone = utcnow()
        closed = org.model_copy(update={"updated_at": gone, "updated_by": ann.id})

        def member_row(user: User) -> OutboxRow:
            return make_user_row(org.id, user).model_copy(update={"kind": "tenancy.user.deleted"})

        def revocation(kind: str, credential_id: UUID, user_id: UUID) -> OutboxRow:
            return revocations_by(ann.id, org.id)(kind, credential_id)

        with pytest.raises(NotFound):
            await storage.write_closed_org(other.id, closed, (), member_row, revocation)
        assert await storage.count_members(org.id) == 2
        assert await storage.read_auth_session(org.id, anns.id) == anns

        rows = await storage.write_closed_org(org.id, closed, (), member_row, revocation)
        assert sorted((r.kind, r.target_id) for r in rows) == sorted(
            [
                ("tenancy.user.deleted", ann.id),
                ("tenancy.user.deleted", bob.id),
                ("tenancy.auth_session.revoked", anns.id),
                ("tenancy.auth_session.revoked", bobs.id),
                ("tenancy.api_key.deleted", key.id),
            ]
        )
        assert rows[0].kind == rows[1].kind == "tenancy.user.deleted", "the users' rows first"
        assert await storage.read_org(org.id) == closed
        assert await storage.count_members(org.id) == 0
        assert await storage.read_users(org.id, None, limit=10) == []
        for user in (ann, bob):
            ended = await storage.read_user(org.id, user.id)
            assert ended is not None and (ended.deleted_at, ended.deleted_by) == (gone, ann.id)
            assert await storage.read_membership_for_user(org.id, user.id) is None
        for session in (anns, bobs):
            revoked = await storage.read_auth_session(org.id, session.id)
            assert revoked is not None and revoked.revoked_at == gone
        deleted = await storage.read_api_key(org.id, key.id)
        assert deleted is not None and deleted.deleted_at == gone
        revoked_invitation = await storage.read_invitation(org.id, pending.id)
        assert revoked_invitation is not None
        assert revoked_invitation.state is InvitationState.REVOKED
        # The other tenant, and the same person there, keep everything.
        assert await storage.read_auth_session(other.id, elsewhere.id) == elsewhere
        assert await storage.read_user(other.id, ann_elsewhere.id) == ann_elsewhere
        assert await storage.read_invitation(other.id, theirs.id) == theirs
        assert await storage.count_members(other.id) == 1
        ours = {r.id for r in rows}
        landed = {(r.org_id, r.target_id) for r in await claim_all(outbox) if r.id in ours}
        assert landed == {(org.id, r.target_id) for r in rows}
        # Closed once, it is not closed again.
        again = closed.model_copy(update={"deleted_at": gone, "deleted_by": ann.id})
        await storage.write_org(org.id, again)
        with pytest.raises(NotFound):
            await storage.write_closed_org(org.id, again, (), member_row, revocation)

    async def test_a_person_is_deleted_in_every_tenant_in_one_commit(
        self, storage: TenancyStorageInterface, outbox: OutboxStorageInterface
    ) -> None:
        """The identity, every user it is (a removed one too), their
        memberships, sessions, keys, and tickets, its sign-ins and its sign-in
        delay go together; each live credential lands the row that announces
        it, and the rows handed in land under their own tenants. Another
        person in the same tenants keeps everything."""
        identity, stays = make_identity(), make_identity()
        team, personal, left = make_org("Team"), make_org("Mine"), make_org("Left")
        bob = make_user(identity.id, identity.email)
        mine = make_user(identity.id, identity.email)
        old = make_user(identity.id, identity.email)
        cid = make_user(stays.id, stays.email)
        await storage.write_identity(identity)
        await storage.write_identity(stays)
        await storage.create_member(team.id, bob, make_membership(bob.id), ())
        await storage.create_member(personal.id, mine, make_membership(mine.id, Role.OWNER), ())
        await storage.create_member(left.id, old, make_membership(old.id), ())
        await storage.write_user(
            left.id, old.model_copy(update={"deleted_at": utcnow(), "deleted_by": old.id})
        )
        cids = make_membership(cid.id)
        await storage.create_member(team.id, cid, cids, ())
        live = make_auth_session(identity.id, bob.id, uuid4().hex)
        dead = make_auth_session(identity.id, bob.id, uuid4().hex).model_copy(
            update={"revoked_at": utcnow()}
        )
        sign_in = make_sign_in(identity.id, uuid4().hex)
        key = make_api_key(mine.id, uuid4().hex)
        ticket = make_socket_ticket(bob.id, uuid4().hex)
        cid_session = make_auth_session(stays.id, cid.id, uuid4().hex)
        cid_key = make_api_key(cid.id, uuid4().hex)
        cid_ticket = make_socket_ticket(cid.id, uuid4().hex)
        await storage.write_api_key(team.id, cid_key)
        await storage.create_socket_ticket(team.id, cid_ticket)
        # An invitation to the person's address, one they accepted, and one
        # to somebody else.
        sent = make_invitation(identity.email)
        accepted = make_invitation("bob.old@example.test").model_copy(
            update={"state": InvitationState.ACCEPTED, "accepted_user_id": bob.id}
        )
        other = make_invitation("dee@example.test")
        await storage.write_invitation(left.id, sent)
        await storage.write_invitation(team.id, accepted)
        await storage.write_invitation(team.id, other)
        await storage.write_auth_session(team.id, live)
        await storage.write_auth_session(team.id, dead)
        await storage.write_auth_session(EMPTY_UUID, sign_in)
        await storage.write_auth_session(team.id, cid_session)
        await storage.write_api_key(personal.id, key)
        await storage.create_socket_ticket(team.id, ticket)
        await storage.record_failed_sign_in(email_digest(identity.email), utcnow())
        await storage.record_failed_sign_in(email_digest(stays.email), utcnow())

        removal = make_user_row(team.id, bob)

        def revocation(org_id: UUID, kind: str, credential_id: UUID) -> OutboxRow:
            return revocations_by(EMPTY_UUID, org_id)(kind, credential_id)

        rows = await storage.delete_person(identity.id, identity.email, (), (removal,), revocation)
        assert sorted((r.org_id, r.kind, r.target_id) for r in rows) == sorted(
            [
                (team.id, "tenancy.auth_session.revoked", live.id),
                (personal.id, "tenancy.api_key.deleted", key.id),
            ]
        )
        assert await storage.read_identity(identity.id) is None
        assert await storage.read_sign_in_delay(email_digest(identity.email)) is None
        for org_id, user in ((team.id, bob), (personal.id, mine), (left.id, old)):
            assert await storage.read_user(org_id, user.id) is None
            assert await storage.read_membership_for_user(org_id, user.id) is None
        for org_id, auth_session_id in ((team.id, live.id), (team.id, dead.id)):
            assert await storage.read_auth_session(org_id, auth_session_id) is None
        assert await storage.read_auth_session_by_id(sign_in.id) is None
        assert await storage.read_api_key(personal.id, key.id) is None
        assert await storage.redeem_socket_ticket(ticket.ticket_hash, utcnow()) is None
        # Everyone else keeps what they had.
        assert await storage.read_identity(stays.id) == stays
        assert await storage.read_sign_in_delay(email_digest(stays.email)) is not None
        assert await storage.read_user(team.id, cid.id) == cid
        assert await storage.read_membership_for_user(team.id, cid.id) == cids
        assert await storage.read_auth_session(team.id, cid_session.id) == cid_session
        assert await storage.read_api_key(team.id, cid_key.id) == cid_key
        assert await storage.read_invitation(left.id, sent.id) is None
        assert await storage.read_invitation(team.id, accepted.id) is None
        assert await storage.read_invitation(team.id, other.id) == other
        assert await storage.redeem_socket_ticket(cid_ticket.ticket_hash, utcnow()) is not None
        ours = {removal.id} | {r.id for r in rows}
        landed = {(r.org_id, r.kind, r.target_id) for r in await claim_all(outbox) if r.id in ours}
        assert landed == {
            (team.id, "tenancy.user.created", bob.id),
            (team.id, "tenancy.auth_session.revoked", live.id),
            (personal.id, "tenancy.api_key.deleted", key.id),
        }
        # Gone already: nothing lands.
        with pytest.raises(NotFound):
            again = (make_user_row(team.id, bob),)
            await storage.delete_person(identity.id, identity.email, (), again, revocation)

    async def test_an_owner_who_leaves_never_leaves_a_tenant_with_none(
        self, storage: TenancyStorageInterface
    ) -> None:
        """The two owners of a tenant leave at once: the owner rows are locked,
        or the memory lock is held, so one goes and the other is refused with
        nothing landed."""
        org = make_org("Team")
        people = [make_identity(), make_identity()]
        users = [make_user(p.id, p.email) for p in people]
        for person, user in zip(people, users, strict=True):
            await storage.write_identity(person)
            await storage.create_member(org.id, user, make_membership(user.id, Role.OWNER), ())

        def revocation(org_id: UUID, kind: str, credential_id: UUID) -> OutboxRow:
            return revocations_by(EMPTY_UUID, org_id)(kind, credential_id)

        async def leave(person: Identity) -> Identity | None:
            try:
                await storage.delete_person(person.id, person.email, (org.id,), (), revocation)
            except Conflict:
                return None
            return person

        run = await race(*(leave(person) for person in people))
        assert len(run.admitted) == 1, run.summary()
        [stayed] = [p for p in people if p not in run.admitted]
        assert await storage.read_identity(stayed.id) == stayed
        assert await storage.count_members(org.id, Role.OWNER) == 1

    async def test_create_org_with_owner_refuses_a_new_identity_on_a_held_email(
        self, storage: TenancyStorageInterface
    ) -> None:
        """A sign-up always brings a new identity; one whose email another
        identity holds meets the unique key, and neither it nor its tenant lands."""
        email = f"{uuid4().hex}@example.test"
        held = make_identity(email)
        await storage.write_identity(held)
        newcomer = make_identity(email)
        org = make_org()
        owner = make_user(newcomer.id, email)
        with pytest.raises(UniqueKeyTaken):
            await storage.create_org_with_owner(
                org.id, org, owner, make_membership(owner.id, Role.OWNER), newcomer
            )
        assert await storage.read_identity(newcomer.id) is None
        assert await storage.read_org(org.id) is None
        assert await storage.read_user(org.id, owner.id) is None
        assert await storage.read_identity_by_email_digest(email_digest(email)) == held

    async def test_a_time_zone_is_written_alone(self, storage: TenancyStorageInterface) -> None:
        """The person's time zone lands on their identity and nothing else
        moves; an identity that does not exist takes none."""
        identity = make_identity()
        await storage.write_identity(identity)
        now = utcnow()
        assert await storage.write_time_zone(identity.id, "Europe/Istanbul", now)
        stored = await storage.read_identity(identity.id)
        assert stored is not None and stored.time_zone == "Europe/Istanbul"
        assert stored == identity.model_copy(
            update={"time_zone": "Europe/Istanbul", "updated_at": now}
        )
        assert await storage.write_time_zone(identity.id, "America/Lima", now)
        again = await storage.read_identity(identity.id)
        assert again is not None and again.time_zone == "America/Lima"
        assert not await storage.write_time_zone(new_id(), "UTC", now), "no such identity"
