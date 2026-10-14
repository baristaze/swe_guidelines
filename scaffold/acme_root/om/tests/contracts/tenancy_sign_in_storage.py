"""The tenancy storage cases of the methods the sign-in delegate calls:
the identities and their link to the identity provider, the second factor,
the sign-in delays, the sign-ins and the sessions they are exchanged for,
and the places an identity holds. The fixtures and the builders it shares
are in `contracts/tenancy_storage.py`, and the cases named in
`CROSS_TENANT_CASES` are the tenant fence's evidence, as there."""

from datetime import timedelta
from uuid import uuid4

import pytest

from acme.om.base import EMPTY_UUID, new_id, utcnow
from acme.om.context import OperatorRole, Role
from acme.om.exceptions import Conflict, NotFound, UniqueKeyTaken
from acme.om.outbox.storage import OutboxStorageInterface
from acme.om.outbox.types.row import OutboxRow
from acme.om.tenancy.rules import email_digest
from acme.om.tenancy.storage import TenancyStorageInterface
from acme.om.tenancy.types.auth_session import AuthSession

from contracts.factories import (
    make_auth_session,
    make_identity,
    make_membership,
    make_operator_token,
    make_org,
    make_sign_in,
    make_user,
)
from contracts.outbox_storage import claim_all
from contracts.racing import race
from contracts.tenancy_storage import TenancyStorageFixtures, make_auth_session_row, revocations_by

INTERFACE = TenancyStorageInterface
"""The storage interface whose cross-tenant cases this suite holds."""

CROSS_TENANT_CASES: frozenset[str] = frozenset(
    {
        "exchange_sign_in",
        "replace_auth_session",
    }
)
"""The methods of `TenancyStorageInterface` whose case presenting another
tenant's identifier is in this module. `test_storage_exceptions.py` adds
up the sets of every module that names the interface and holds them to
every method that takes a tenant, so a new method arrives with its case."""


class TenancySignInStorageContract(TenancyStorageFixtures):
    async def test_identity_is_global(self, storage: TenancyStorageInterface) -> None:
        identity = make_identity()
        await storage.write_identity(identity)
        assert await storage.read_identity(identity.id) == identity
        assert await storage.read_identity_by_email_digest(email_digest(identity.email)) == identity
        assert (
            await storage.read_identity_by_email_digest(email_digest("nobody@example.test")) is None
        )

    # Each unique key the schema declares on these rows has a case here, so
    # the memory impl refuses what the engine refuses: the write raises
    # UniqueKeyTaken and the row that held the key is unchanged. An update by
    # copy of the row that holds the key passes.

    async def test_identity_email_is_unique(self, storage: TenancyStorageInterface) -> None:
        email = f"{uuid4().hex}@example.test"
        identity = make_identity(email)
        await storage.write_identity(identity)
        with pytest.raises(UniqueKeyTaken):
            await storage.write_identity(make_identity(email))
        assert await storage.read_identity_by_email_digest(email_digest(email)) == identity
        promoted = identity.model_copy(update={"operator_role": OperatorRole.READ})
        await storage.write_identity(promoted)
        assert await storage.read_identity(identity.id) == promoted

    async def test_session_token_hash_is_unique(self, storage: TenancyStorageInterface) -> None:
        org, other_org = make_org(), make_org("Other")
        token_hash = uuid4().hex
        session = make_auth_session(new_id(), new_id(), token_hash)
        assert await storage.create_auth_session(org.id, session)
        with pytest.raises(UniqueKeyTaken):
            await storage.create_auth_session(
                other_org.id, make_auth_session(new_id(), new_id(), token_hash)
            )
        with pytest.raises(UniqueKeyTaken):
            await storage.write_auth_session(
                other_org.id, make_auth_session(new_id(), new_id(), token_hash)
            )
        assert await storage.read_auth_session_by_digest(token_hash) == (org.id, session)
        revoked = session.model_copy(update={"revoked_at": utcnow()})
        await storage.write_auth_session(org.id, revoked)
        assert await storage.read_auth_session(org.id, session.id) == revoked

    async def test_a_session_is_created_once(self, storage: TenancyStorageInterface) -> None:
        """The create is the insert that reports: a second create of the id
        says so, and the row keeps the first credential's digest, expiry, and
        holder."""
        org = make_org()
        session = make_auth_session(new_id(), new_id(), uuid4().hex)
        assert await storage.create_auth_session(org.id, session)
        again = make_auth_session(new_id(), new_id(), uuid4().hex).model_copy(
            update={"id": session.id, "expires_at": session.expires_at + timedelta(days=1)}
        )
        assert not await storage.create_auth_session(org.id, again)
        assert await storage.read_auth_session(org.id, session.id) == session
        assert await storage.read_auth_session_by_digest(again.token_hash) is None
        assert await storage.read_auth_session_by_digest(session.token_hash) == (org.id, session)

    async def test_the_memberships_of_an_identity_span_tenants_and_skip_the_gone(
        self, storage: TenancyStorageInterface
    ) -> None:
        identity = make_identity()
        places = []
        for name in ("A", "B", "C", "D"):
            org = make_org(name)
            user = make_user(identity.id)
            role = Role.OWNER if name == "A" else Role.MEMBER
            await storage.create_org_with_owner(org.id, org, user, make_membership(user.id, role))
            places.append((org, user, role))
        # Someone else's place is never listed.
        stranger_org = make_org("E")
        stranger = make_user(make_identity().id)
        await storage.create_org_with_owner(
            stranger_org.id, stranger_org, stranger, make_membership(stranger.id, Role.OWNER)
        )
        # A deleted org, and a removed user, are places nobody holds.
        gone_org, _, _ = places[1]
        await storage.write_org(
            gone_org.id,
            gone_org.model_copy(update={"deleted_at": utcnow(), "deleted_by": new_id()}),
        )
        left_org, left_user, _ = places[2]
        ended = await storage.read_membership_for_user(left_org.id, left_user.id)
        assert ended is not None
        now = utcnow()
        await storage.remove_member(
            left_org.id,
            left_user.model_copy(update={"deleted_at": now, "deleted_by": new_id()}),
            ended.model_copy(update={"deleted_at": now, "deleted_by": new_id()}),
            (),
            revocations_by(left_user.id, left_org.id),
        )
        live = sorted((places[0], places[3]), key=lambda place: place[1].id)
        found = await storage.read_memberships_by_identity(identity.id, limit=10)
        assert [(m.org.id, m.user.id, m.role) for m in found] == [
            (org.id, user.id, role) for org, user, role in live
        ], "the living, by user id"
        assert found[0].org == live[0][0] and found[0].user == live[0][1]
        # The bound and the cursor cut at the same rows in both impls.
        first = await storage.read_memberships_by_identity(identity.id, limit=1)
        assert first == found[:1]
        rest = await storage.read_memberships_by_identity(identity.id, 10, first[0].user.id)
        assert rest == found[1:]

    async def test_a_session_is_found_by_id_across_tenants(
        self, storage: TenancyStorageInterface
    ) -> None:
        org = make_org()
        session = make_auth_session(new_id(), new_id(), uuid4().hex)
        await storage.write_auth_session(org.id, session)
        assert await storage.read_auth_session_by_id(session.id) == (org.id, session)
        assert await storage.read_auth_session_by_id(new_id()) is None

    async def test_a_session_keeps_the_provider_session_behind_it(
        self, storage: TenancyStorageInterface
    ) -> None:
        org = make_org()
        session = make_auth_session(new_id(), new_id(), uuid4().hex).model_copy(
            update={"provider_session_id": "session_01K5PROVIDER"}
        )
        await storage.write_auth_session(org.id, session)
        found = await storage.read_auth_session_by_digest(session.token_hash)
        assert found is not None and found[1].provider_session_id == "session_01K5PROVIDER"

    async def test_replace_session_ends_one_and_lands_the_other_in_one_commit(
        self, storage: TenancyStorageInterface, outbox: OutboxStorageInterface
    ) -> None:
        org_a, org_b = make_org("A"), make_org("B")
        identity_id = new_id()
        old = make_auth_session(identity_id, new_id(), uuid4().hex)
        await storage.write_auth_session(org_a.id, old)
        new = make_auth_session(identity_id, new_id(), uuid4().hex)
        ended = old.model_copy(update={"revoked_at": utcnow()})
        row = make_auth_session_row(org_a.id, ended)
        await storage.replace_auth_session(org_b.id, new, org_a.id, ended, (row,))
        assert await storage.read_auth_session(org_a.id, old.id) == ended
        assert await storage.read_auth_session(org_b.id, new.id) == new
        assert await storage.read_auth_session_by_digest(new.token_hash) == (org_b.id, new)
        landed = [r for r in await claim_all(outbox) if r.target_id == old.id]
        assert [(r.id, r.org_id) for r in landed] == [(row.id, org_a.id)]
        # An ended session cannot be ended again: two switches admit one.
        again = make_auth_session(identity_id, new_id(), uuid4().hex)
        with pytest.raises(Conflict):
            await storage.replace_auth_session(org_b.id, again, org_a.id, ended, ())
        assert await storage.read_auth_session(org_b.id, again.id) is None

    async def test_two_replacements_of_one_session_admit_one(
        self, storage: TenancyStorageInterface
    ) -> None:
        """Two tabs switching on one session at once: the row lock, or the memory
        lock, lets one through and the other finds the session ended."""
        org_a, org_b = make_org("A"), make_org("B")
        old = make_auth_session(new_id(), new_id(), uuid4().hex)
        await storage.write_auth_session(org_a.id, old)
        ended = old.model_copy(update={"revoked_at": utcnow()})
        candidates = [make_auth_session(old.identity_id, new_id(), uuid4().hex) for _ in range(3)]

        async def switch(new: AuthSession) -> AuthSession | None:
            try:
                await storage.replace_auth_session(org_b.id, new, org_a.id, ended, ())
            except Conflict:
                return None
            return new

        run = await race(*(switch(new) for new in candidates))
        assert len(run.admitted) == 1, run.summary()
        landed = [await storage.read_auth_session(org_b.id, new.id) for new in candidates]
        assert [s for s in landed if s is not None] == run.admitted

    async def test_replace_session_never_ends_another_tenants_session(
        self, storage: TenancyStorageInterface
    ) -> None:
        """The ended session is named with its tenant; named with another one,
        nothing is ended and nothing lands."""
        org_a, org_b = make_org("A"), make_org("B")
        old = make_auth_session(new_id(), new_id(), uuid4().hex)
        await storage.write_auth_session(org_a.id, old)
        new = make_auth_session(old.identity_id, new_id(), uuid4().hex)
        ended = old.model_copy(update={"revoked_at": utcnow()})
        with pytest.raises(NotFound):
            await storage.replace_auth_session(org_b.id, new, org_b.id, ended, ())
        assert await storage.read_auth_session(org_a.id, old.id) == old
        assert await storage.read_auth_session(org_b.id, new.id) is None
        assert await storage.read_auth_session_by_digest(new.token_hash) is None

    async def test_replace_session_lands_nothing_when_the_new_token_is_taken(
        self, storage: TenancyStorageInterface
    ) -> None:
        org_a, org_b = make_org("A"), make_org("B")
        taken = make_auth_session(new_id(), new_id(), uuid4().hex)
        await storage.write_auth_session(org_b.id, taken)
        old = make_auth_session(new_id(), new_id(), uuid4().hex)
        await storage.write_auth_session(org_a.id, old)
        new = make_auth_session(old.identity_id, new_id(), taken.token_hash)
        ended = old.model_copy(update={"revoked_at": utcnow()})
        with pytest.raises(UniqueKeyTaken):
            await storage.replace_auth_session(org_b.id, new, org_a.id, ended, ())
        assert await storage.read_auth_session(org_a.id, old.id) == old, "the old one still lives"
        assert await storage.read_auth_session(org_b.id, new.id) is None

    async def test_exchange_sign_in_ends_it_and_lands_the_session_in_one_commit(
        self, storage: TenancyStorageInterface
    ) -> None:
        org = make_org()
        sign_in = make_sign_in(new_id(), uuid4().hex)
        await storage.write_auth_session(EMPTY_UUID, sign_in)
        new = make_auth_session(sign_in.identity_id, new_id(), uuid4().hex)
        ended = sign_in.model_copy(update={"revoked_at": utcnow()})
        await storage.exchange_sign_in(org.id, new, ended)
        assert await storage.read_auth_session_by_id(sign_in.id) == (EMPTY_UUID, ended)
        assert await storage.read_auth_session(org.id, new.id) == new
        assert await storage.read_auth_session_by_digest(new.token_hash) == (org.id, new)
        # An ended sign-in cannot be exchanged again: a sign-in makes one session.
        again = make_auth_session(sign_in.identity_id, new_id(), uuid4().hex)
        with pytest.raises(Conflict):
            await storage.exchange_sign_in(org.id, again, ended)
        assert await storage.read_auth_session(org.id, again.id) is None
        assert await storage.read_auth_session_by_digest(again.token_hash) is None

    async def test_two_exchanges_of_one_sign_in_admit_one(
        self, storage: TenancyStorageInterface
    ) -> None:
        """Clicks the browser delivered together, or a replayed request: the
        row lock, or the memory lock, lets one through and the others find
        the sign-in ended."""
        org = make_org()
        sign_in = make_sign_in(new_id(), uuid4().hex)
        await storage.write_auth_session(EMPTY_UUID, sign_in)
        ended = sign_in.model_copy(update={"revoked_at": utcnow()})
        candidates = [
            make_auth_session(sign_in.identity_id, new_id(), uuid4().hex) for _ in range(3)
        ]

        async def exchange(new: AuthSession) -> AuthSession | None:
            try:
                await storage.exchange_sign_in(org.id, new, ended)
            except Conflict:
                return None
            return new

        run = await race(*(exchange(new) for new in candidates))
        assert len(run.admitted) == 1, run.summary()
        landed = [await storage.read_auth_session(org.id, new.id) for new in candidates]
        assert [s for s in landed if s is not None] == run.admitted

    async def test_exchange_sign_in_never_ends_a_tenants_session(
        self, storage: TenancyStorageInterface
    ) -> None:
        """Only a row of the system scope is a sign-in. A tenant's session
        named in its place is not found there: nothing is ended and nothing
        lands, in that tenant or in another."""
        org_a, org_b = make_org("A"), make_org("B")
        held = make_auth_session(new_id(), new_id(), uuid4().hex)
        await storage.write_auth_session(org_a.id, held)
        new = make_auth_session(held.identity_id, new_id(), uuid4().hex)
        ended = held.model_copy(update={"revoked_at": utcnow()})
        for org in (org_a, org_b):
            with pytest.raises(NotFound):
                await storage.exchange_sign_in(org.id, new, ended)
            assert await storage.read_auth_session(org.id, new.id) is None
        assert await storage.read_auth_session(org_a.id, held.id) == held
        assert await storage.read_auth_session_by_digest(new.token_hash) is None

    async def test_exchange_sign_in_ends_nothing_when_the_new_token_is_taken(
        self, storage: TenancyStorageInterface
    ) -> None:
        org = make_org()
        taken = make_auth_session(new_id(), new_id(), uuid4().hex)
        await storage.write_auth_session(org.id, taken)
        sign_in = make_sign_in(new_id(), uuid4().hex)
        await storage.write_auth_session(EMPTY_UUID, sign_in)
        new = make_auth_session(sign_in.identity_id, new_id(), taken.token_hash)
        ended = sign_in.model_copy(update={"revoked_at": utcnow()})
        with pytest.raises(UniqueKeyTaken):
            await storage.exchange_sign_in(org.id, new, ended)
        assert await storage.read_auth_session_by_id(sign_in.id) == (EMPTY_UUID, sign_in), (
            "still live"
        )
        assert await storage.read_auth_session(org.id, new.id) is None

    async def test_exchange_sign_in_lands_an_operator_token_in_the_system_scope(
        self, storage: TenancyStorageInterface
    ) -> None:
        """A sign-in with its second factor mints one operator token: the
        token lands beside the sign-in, in the system scope, and the sign-in
        ends in the same commit, so a second mint with it lands nothing."""
        sign_in = make_sign_in(new_id(), uuid4().hex)
        await storage.write_auth_session(EMPTY_UUID, sign_in)
        token = make_operator_token(sign_in.identity_id, uuid4().hex)
        ended = sign_in.model_copy(update={"revoked_at": utcnow()})
        await storage.exchange_sign_in(EMPTY_UUID, token, ended)
        assert await storage.read_auth_session_by_id(sign_in.id) == (EMPTY_UUID, ended)
        assert await storage.read_auth_session_by_digest(token.token_hash) == (EMPTY_UUID, token)
        again = make_operator_token(sign_in.identity_id, uuid4().hex)
        with pytest.raises(Conflict):
            await storage.exchange_sign_in(EMPTY_UUID, again, ended)
        assert await storage.read_auth_session_by_digest(again.token_hash) is None

    async def test_failed_sign_ins_made_at_once_are_each_counted(
        self, storage: TenancyStorageInterface
    ) -> None:
        # Guesses at one email arrive together; the count moves in the
        # statement, so none of them is lost to a read written back by
        # another. The email need not belong to anyone.
        digest = email_digest(f"{uuid4().hex}@example.test")
        assert await storage.read_sign_in_delay(digest) is None
        failed_at = utcnow()
        await race(*(storage.record_failed_sign_in(digest, failed_at) for _ in range(5)))
        counted = await storage.read_sign_in_delay(digest)
        assert counted is not None
        assert (counted.failures, counted.last_failed_at) == (5, failed_at)
        await storage.clear_failed_sign_ins(digest)
        assert await storage.read_sign_in_delay(digest) is None

    async def test_the_email_digest_finds_the_identity_the_rule_names(
        self, storage: TenancyStorageInterface
    ) -> None:
        """The database computes the digest of the stored address, folded, and
        the rule computes it of the given one, folded; the two agree in any
        spelling, non-ASCII included."""
        email = f"zo\u00eb-{uuid4().hex[:8]}@example.test"
        identity = make_identity(email)
        await storage.write_identity(identity)
        assert await storage.read_identity_by_email_digest(email_digest(email)) == identity
        assert await storage.read_identity_by_email_digest(email_digest(email.upper())) == identity
        other = email.replace("zo", "zoe", 1)
        assert await storage.read_identity_by_email_digest(email_digest(other)) is None

    async def test_one_identity_holds_an_address_in_any_case(
        self, storage: TenancyStorageInterface
    ) -> None:
        """The unique index is on the folded digest, so a writer that keeps a
        second spelling of an address, one that does not fold among them,
        meets it, and nothing lands."""
        email = f"dee-{uuid4().hex[:8]}@example.test"
        identity = make_identity(email)
        await storage.write_identity(identity)
        second = make_identity(email.upper())
        with pytest.raises(UniqueKeyTaken):
            await storage.write_identity(second)
        assert await storage.read_identity(second.id) is None

    async def test_an_identity_write_lands_its_audit_row_under_the_system_scope(
        self, storage: TenancyStorageInterface, outbox: OutboxStorageInterface
    ) -> None:
        identity = make_identity()
        await storage.write_identity(identity)
        changed = identity.model_copy(update={"operator_role": OperatorRole.READ})
        row = OutboxRow(
            id=new_id(),
            created_at=utcnow(),
            org_id=EMPTY_UUID,
            kind="tenancy.operator.granted",
            target_id=identity.id,
            payload={},
            actor_id=new_id(),
            request_id=new_id(),
            app="cli",
        )
        await storage.write_identity(changed, (row,))
        assert await storage.read_identity(identity.id) == changed
        assert [r.org_id for r in await claim_all(outbox) if r.id == row.id] == [EMPTY_UUID]

    async def test_a_totp_secret_is_minted_confirmed_and_each_step_used_once(
        self, storage: TenancyStorageInterface
    ) -> None:
        identity = make_identity()
        await storage.write_identity(identity)
        now = utcnow()
        assert not await storage.confirm_totp(identity.id, 10, now), "no secret yet"
        assert not await storage.use_totp_step(identity.id, 10), "nothing is enrolled"
        assert await storage.write_totp_secret(identity.id, "v1.first", now)
        assert await storage.write_totp_secret(identity.id, "v1.second", now), "unconfirmed"
        assert await storage.confirm_totp(identity.id, 10, now)
        assert not await storage.confirm_totp(identity.id, 11, now), "confirmed once"
        assert not await storage.write_totp_secret(identity.id, "v1.third", now), "enrolled"
        stored = await storage.read_identity(identity.id)
        assert stored is not None and stored.totp_enrolled
        assert (stored.totp_secret, stored.totp_last_step) == ("v1.second", 10)
        assert stored.totp_confirmed_at == now
        # A step is accepted once, and never one before the last.
        assert not await storage.use_totp_step(identity.id, 10)
        assert not await storage.use_totp_step(identity.id, 9)
        run = await race(*(storage.use_totp_step(identity.id, 11) for _ in range(5)))
        assert run.outcomes.count(True) == 1, run.summary()
        assert not await storage.write_totp_secret(new_id(), "v1.x", now), "no such identity"

    # The identity provider's link.

    async def test_an_identity_is_found_by_its_issuer_and_subject(
        self, storage: TenancyStorageInterface
    ) -> None:
        identity = make_identity().model_copy(
            update={"issuer": "https://issuer.test", "subject": f"user_{uuid4().hex}"}
        )
        await storage.write_identity(identity)
        assert identity.subject is not None
        found = await storage.read_identity_by_issuer_subject(
            "https://issuer.test", identity.subject
        )
        assert found == identity
        assert (
            await storage.read_identity_by_issuer_subject("https://other.test", identity.subject)
            is None
        )
        assert (
            await storage.read_identity_by_issuer_subject("https://issuer.test", "nobody") is None
        )

    async def test_one_identity_per_subject_of_an_issuer(
        self, storage: TenancyStorageInterface
    ) -> None:
        subject = f"user_{uuid4().hex}"
        first = make_identity().model_copy(
            update={"issuer": "https://issuer.test", "subject": subject}
        )
        await storage.write_identity(first)
        second = make_identity().model_copy(
            update={"issuer": "https://issuer.test", "subject": subject}
        )
        with pytest.raises(UniqueKeyTaken):
            await storage.write_identity(second)
        assert await storage.read_identity(second.id) is None
        # The same subject under another issuer is another person, and an
        # identity with no subject never collides.
        await storage.write_identity(second.model_copy(update={"issuer": "https://other.test"}))
        await storage.write_identity(make_identity())
        await storage.write_identity(make_identity())
