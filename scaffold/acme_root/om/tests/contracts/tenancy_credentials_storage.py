"""The tenancy storage cases of the methods the credentials delegate
calls: the api keys and their re-mint under an idempotency marker, and the
sessions a person lists and revokes. The fixtures and the builders it
shares are in `contracts/tenancy_storage.py`, and the cases named in
`CROSS_TENANT_CASES` are the tenant fence's evidence, as there."""

from datetime import timedelta
from uuid import UUID, uuid4

import pytest

from acme.om.base import new_id, utcnow
from acme.om.exceptions import Conflict, TenantMismatch, UniqueKeyTaken
from acme.om.idempotency.storage import IdempotencyStorageInterface
from acme.om.idempotency.types.attempt import lease_bound
from acme.om.idempotency.types.record import IdempotencyRecord
from acme.om.outbox.types.row import OutboxRow
from acme.om.tenancy.storage import TenancyStorageInterface
from acme.om.tenancy.types.api_key import ApiKey

from contracts.factories import make_api_key, make_auth_session, make_identity, make_org, make_user
from contracts.tenancy_storage import TenancyStorageFixtures, make_auth_session_row

INTERFACE = TenancyStorageInterface
"""The storage interface whose cross-tenant cases this suite holds."""

CROSS_TENANT_CASES: frozenset[str] = frozenset(
    {
        "create_auth_session",
        "issue_api_key",
        "read_api_key",
        "read_api_keys",
        "read_auth_session",
        "read_auth_sessions",
        "write_api_key",
        "write_auth_session",
    }
)
"""The methods of `TenancyStorageInterface` whose case presenting another
tenant's identifier is in this module. `test_storage_exceptions.py` adds
up the sets of every module that names the interface and holds them to
every method that takes a tenant, so a new method arrives with its case."""


def make_marker(api_key: ApiKey, attempt_id: UUID) -> IdempotencyRecord:
    """The pending marker a creating request runs under, on the key's id: what
    the re-mint of a rerun is fenced on. A marker is per (tenant, user, key),
    so each attempt in a test that needs two markers carries its own key."""
    return IdempotencyRecord(
        id=new_id(),
        created_at=utcnow(),
        user_id=api_key.user_id,
        key=f"key-{attempt_id}",
        request_digest="digest",
        target_id=api_key.id,
        attempt_id=attempt_id,
    )


def make_key_row(org_id: UUID, api_key: ApiKey) -> OutboxRow:
    """The outbox row a key's create lands with; the memory outbox the root
    wires receives it, the Postgres one inserts it in the same commit."""
    return OutboxRow(
        id=new_id(),
        created_at=utcnow(),
        org_id=org_id,
        kind="tenancy.api_key.created",
        target_id=api_key.id,
        payload={"name": api_key.name},
        actor_id=api_key.user_id,
        request_id=new_id(),
        app="api",
    )


class TenancyCredentialsStorageContract(TenancyStorageFixtures):
    async def test_the_session_reads_and_writes_are_tenant_scoped(
        self, storage: TenancyStorageInterface
    ) -> None:
        """A session list is personal to a user inside a tenant, so both keys
        are in the query: the same user id under another tenant lists nothing,
        and a create or a revocation written from there lands nothing."""
        org_a, org_b = make_org("A"), make_org("B")
        user_id = new_id()
        session = make_auth_session(new_id(), user_id, uuid4().hex)
        assert await storage.create_auth_session(org_a.id, session)
        assert await storage.read_auth_sessions(org_b.id, user_id, utcnow(), limit=10) == []
        assert await storage.read_auth_session(org_b.id, session.id) is None
        assert not await storage.create_auth_session(
            org_b.id, session.model_copy(update={"token_hash": uuid4().hex})
        )
        with pytest.raises(TenantMismatch):
            await storage.write_auth_session(
                org_b.id, session.model_copy(update={"revoked_at": utcnow()})
            )
        assert await storage.read_auth_session(org_a.id, session.id) == session
        assert await storage.read_auth_sessions(org_a.id, user_id, utcnow(), limit=10) == [session]

    async def test_the_api_key_reads_the_page_and_the_writes_are_tenant_scoped(
        self, storage: TenancyStorageInterface
    ) -> None:
        """The key list pages newest first. The cursor of another tenant's page
        carries nothing across, with the owner filter on or off."""
        org_a, org_b = make_org("A"), make_org("B")
        user_id = new_id()
        keys = [make_api_key(user_id, uuid4().hex) for _ in range(3)]
        for key in keys:
            await storage.write_api_key(org_a.id, key)
        newest_first = sorted(keys, key=lambda k: k.id, reverse=True)
        assert await storage.read_api_keys(org_b.id, None, limit=10) == []
        assert await storage.read_api_keys(org_b.id, None, limit=10, user_id=user_id) == []
        assert await storage.read_api_keys(org_b.id, newest_first[0].id, limit=10) == []
        assert await storage.read_api_key(org_b.id, keys[0].id) is None
        with pytest.raises(TenantMismatch):
            await storage.write_api_key(org_b.id, keys[0].model_copy(update={"name": "stolen"}))
        assert await storage.read_api_keys(org_a.id, None, limit=10) == newest_first

    async def test_a_rerun_from_another_tenant_never_re_mints_the_secret(
        self, storage: TenancyStorageInterface, markers: IdempotencyStorageInterface
    ) -> None:
        """The re-mint is one conditional write, and the tenant is one of the
        conditions in its own `WHERE`. A rerun that presents the key id of
        another tenant, holding a marker of its own, is refused and the
        stored digest stays as it was."""
        org, other = make_org("A"), make_org("B")
        api_key = make_api_key(new_id(), uuid4().hex)
        attempt_id = new_id()
        await markers.write_record(org.id, make_marker(api_key, attempt_id))
        await markers.write_record(other.id, make_marker(api_key, attempt_id))
        assert await storage.issue_api_key(
            org.id, api_key, (make_key_row(org.id, api_key),), attempt_id
        ) == (api_key, True)
        rerun = api_key.model_copy(update={"key_hash": uuid4().hex, "updated_at": utcnow()})
        with pytest.raises(Conflict):
            await storage.issue_api_key(
                other.id, rerun, (make_key_row(other.id, rerun),), attempt_id
            )
        assert await storage.read_api_key(org.id, api_key.id) == api_key
        assert await storage.read_api_key(other.id, api_key.id) is None
        assert await storage.read_api_key_by_digest(rerun.key_hash) is None

    # Each unique key the schema declares on these rows has a case here, so
    # the memory impl refuses what the engine refuses: the write raises
    # UniqueKeyTaken and the row that held the key is unchanged. An update by
    # copy of the row that holds the key passes.

    async def test_api_key_hash_is_unique(self, storage: TenancyStorageInterface) -> None:
        org, other_org = make_org(), make_org("Other")
        key_hash = uuid4().hex
        api_key = make_api_key(new_id(), key_hash)
        await storage.write_api_key(org.id, api_key)
        with pytest.raises(UniqueKeyTaken):
            await storage.write_api_key(other_org.id, make_api_key(new_id(), key_hash))
        # The create refuses it too, and lands nothing under the new id.
        minted = make_api_key(new_id(), key_hash)
        with pytest.raises(UniqueKeyTaken):
            await storage.issue_api_key(org.id, minted, (make_key_row(org.id, minted),), None)
        assert await storage.read_api_key(org.id, minted.id) is None
        assert await storage.read_api_key_by_digest(key_hash) == (org.id, api_key)
        renamed = api_key.model_copy(update={"name": "renamed"})
        await storage.write_api_key(org.id, renamed)
        assert await storage.read_api_key(org.id, api_key.id) == renamed

    async def test_a_session_write_lands_its_outbox_row_beside_it(
        self, storage: TenancyStorageInterface
    ) -> None:
        """A revocation is a session write with a handoff: the row and its
        outbox row land together, as a user's or a key's do."""
        org = make_org()
        identity = make_identity()
        user = make_user(identity.id)
        session = make_auth_session(
            identity.id, user.id, uuid4().hex
        )  # unique across runs of a shared database
        await storage.write_auth_session(org.id, session)
        revoked = session.model_copy(update={"revoked_at": utcnow()})
        await storage.write_auth_session(org.id, revoked, (make_auth_session_row(org.id, revoked),))
        assert await storage.read_auth_session(org.id, session.id) == revoked

    async def test_sessions_of_a_user_are_the_live_ones_newest_first(
        self, storage: TenancyStorageInterface
    ) -> None:
        org = make_org()
        identity_id, user_id = new_id(), new_id()
        now = utcnow()
        sessions = [make_auth_session(identity_id, user_id, uuid4().hex) for _ in range(3)]
        for session in reversed(sessions):
            await storage.write_auth_session(org.id, session)
        await storage.write_auth_session(
            org.id, make_auth_session(identity_id, new_id(), uuid4().hex)
        )
        listed = await storage.read_auth_sessions(org.id, user_id, now, limit=10)
        assert listed == sorted(sessions, key=lambda s: s.id, reverse=True)
        assert len(await storage.read_auth_sessions(org.id, user_id, now, limit=2)) == 2
        revoked = sessions[0].model_copy(update={"revoked_at": now})
        await storage.write_auth_session(org.id, revoked)
        assert revoked not in await storage.read_auth_sessions(org.id, user_id, now, limit=10)
        assert await storage.read_auth_session(org.id, revoked.id) == revoked
        # Expiry is the storage's filter too, before the clamp: a page of dead
        # sessions never hides a live one.
        expired = [
            make_auth_session(identity_id, user_id, uuid4().hex, ttl=timedelta(seconds=-1))
            for _ in range(3)
        ]
        for session in expired:
            await storage.write_auth_session(org.id, session)
        live = make_auth_session(identity_id, user_id, uuid4().hex)
        await storage.write_auth_session(org.id, live)
        assert await storage.read_auth_sessions(org.id, user_id, utcnow(), limit=1) == [live]
        assert await storage.read_auth_sessions(
            org.id, user_id, now - timedelta(hours=2), limit=10
        ) == [
            live,
            *expired[::-1],
            sessions[2],
            sessions[1],
        ]

    async def test_api_keys_are_listed_newest_first_and_filtered_by_owner_before_the_clamp(
        self, storage: TenancyStorageInterface
    ) -> None:
        org = make_org()
        ann, bob = new_id(), new_id()
        anns = [make_api_key(ann, uuid4().hex) for _ in range(2)]
        bobs = make_api_key(bob, uuid4().hex)
        for key in (*anns, bobs):
            await storage.write_api_key(org.id, key)
        assert await storage.read_api_keys(org.id, None, limit=10) == [bobs, anns[1], anns[0]]
        assert await storage.read_api_keys(org.id, None, limit=2) == [bobs, anns[1]]
        assert await storage.read_api_keys(org.id, None, limit=1, user_id=ann) == [anns[1]]
        assert await storage.read_api_keys(org.id, None, limit=10, user_id=bob) == [bobs]
        assert await storage.read_api_keys(org.id, None, limit=10, user_id=new_id()) == []

    async def test_the_api_key_list_pages_after_a_cursor(
        self, storage: TenancyStorageInterface
    ) -> None:
        """The key list reads newest first, so its next page is what sorts
        below the cursor; the owner filter travels with it."""
        org = make_org()
        ann = new_id()
        anns = [make_api_key(ann, uuid4().hex) for _ in range(5)]
        for key in anns:
            await storage.write_api_key(org.id, key)
        newest_first = sorted(anns, key=lambda k: k.id, reverse=True)
        paged: list[ApiKey] = []
        after: UUID | None = None
        while page := await storage.read_api_keys(org.id, after, limit=2):
            paged += page
            after = page[-1].id
        assert paged == newest_first
        assert await storage.read_api_keys(org.id, newest_first[-1].id, limit=10) == []
        assert (
            await storage.read_api_keys(org.id, newest_first[0].id, limit=2, user_id=ann)
            == (newest_first[1:3])
        )

    async def test_issue_api_key_creates_once_and_reissues_the_secret_on_a_rerun(
        self, storage: TenancyStorageInterface, markers: IdempotencyStorageInterface
    ) -> None:
        org = make_org()
        user_id = new_id()
        first_hash, second_hash = uuid4().hex, uuid4().hex
        api_key = make_api_key(user_id, first_hash)
        attempt_id = new_id()
        await markers.write_record(org.id, make_marker(api_key, attempt_id))
        assert await storage.issue_api_key(
            org.id, api_key, (make_key_row(org.id, api_key),), attempt_id
        ) == (
            api_key,
            True,
        )
        # The rerun presents the same id with a new digest, and a name and a
        # clock of its own; only the digest (and the update stamp) lands. The
        # marker still holds the attempt making it, so it is admitted.
        later = utcnow() + timedelta(seconds=1)
        rerun = api_key.model_copy(
            update={"key_hash": second_hash, "name": "renamed", "updated_at": later}
        )
        stored, created = await storage.issue_api_key(
            org.id, rerun, (make_key_row(org.id, rerun),), attempt_id
        )
        assert created is False
        assert (stored.id, stored.name, stored.created_at) == (
            api_key.id,
            api_key.name,
            api_key.created_at,
        )
        assert (stored.key_hash, stored.updated_at) == (second_hash, later)
        assert await storage.read_api_key(org.id, api_key.id) == stored
        assert await storage.read_api_key_by_digest(first_hash) is None
        assert await storage.read_api_key_by_digest(second_hash) == (org.id, stored)
        # Another issuer presenting the id is refused and changes nothing.
        other = rerun.model_copy(update={"user_id": new_id(), "key_hash": uuid4().hex})
        with pytest.raises(Conflict):
            await storage.issue_api_key(org.id, other, (make_key_row(org.id, other),), attempt_id)
        assert await storage.read_api_key(org.id, api_key.id) == stored
        # A request that carried no key holds no marker, so it never re-mints.
        keyless = rerun.model_copy(update={"key_hash": uuid4().hex})
        with pytest.raises(Conflict):
            await storage.issue_api_key(org.id, keyless, (make_key_row(org.id, keyless),), None)
        assert await storage.read_api_key(org.id, api_key.id) == stored

    async def test_a_rerun_never_re_mints_a_revoked_key(
        self, storage: TenancyStorageInterface, markers: IdempotencyStorageInterface
    ) -> None:
        """A create retried after the key was revoked must not put a live
        secret back on a dead row and answer 201 with it, marker or no
        marker."""
        org = make_org()
        user_id = new_id()
        api_key = make_api_key(user_id, uuid4().hex)
        attempt_id = new_id()
        await markers.write_record(org.id, make_marker(api_key, attempt_id))
        await storage.issue_api_key(org.id, api_key, (make_key_row(org.id, api_key),), attempt_id)
        revoked = api_key.model_copy(update={"deleted_at": utcnow(), "deleted_by": user_id})
        await storage.write_api_key(org.id, revoked)
        rerun = api_key.model_copy(update={"key_hash": uuid4().hex, "updated_at": utcnow()})
        with pytest.raises(Conflict):
            await storage.issue_api_key(org.id, rerun, (make_key_row(org.id, rerun),), attempt_id)
        assert await storage.read_api_key(org.id, api_key.id) == revoked
        assert await storage.read_api_key_by_digest(rerun.key_hash) is None

    async def test_a_rerun_never_re_mints_a_key_the_marker_no_longer_holds(
        self, storage: TenancyStorageInterface, markers: IdempotencyStorageInterface
    ) -> None:
        """An attempt that ran past the idempotency marker's pending lease is a
        zombie: the retry that took the marker over already handed its key to
        the caller, and the zombie's rerun must not overwrite it. Both attempts
        stamped the same `created_at`, which is why no ordering can tell them
        apart and only the marker can."""
        org = make_org()
        user_id = new_id()
        stalled_attempt, winning_attempt = new_id(), new_id()
        stalled = make_api_key(user_id, uuid4().hex)
        winner = stalled.model_copy(update={"key_hash": uuid4().hex})
        assert winner.created_at == stalled.created_at
        marker = make_marker(stalled, stalled_attempt)
        await markers.write_record(org.id, marker)
        # The first attempt lands the row, then stalls before its outcome.
        await storage.issue_api_key(
            org.id, stalled, (make_key_row(org.id, stalled),), stalled_attempt
        )
        # The retry takes the marker over past the lease and reruns on the same
        # id: its re-mint is admitted, and its secret is the one the caller has.
        taken = await markers.take_over_pending(
            org.id, user_id, marker.key, lease_bound(utcnow()), winning_attempt
        )
        assert taken is not None
        await storage.issue_api_key(
            org.id, winner, (make_key_row(org.id, winner),), winning_attempt
        )
        # The zombie wakes. Nothing about the two rows can be ordered; the
        # marker it no longer holds is what refuses it.
        with pytest.raises(Conflict):
            await storage.issue_api_key(
                org.id, stalled, (make_key_row(org.id, stalled),), stalled_attempt
            )
        live = await storage.read_api_key_by_digest(winner.key_hash)
        assert live is not None and live[1].id == winner.id
        assert await storage.read_api_key_by_digest(stalled.key_hash) is None

    async def test_a_rerun_whose_clock_ran_ahead_is_refused_too(
        self, storage: TenancyStorageInterface, markers: IdempotencyStorageInterface
    ) -> None:
        """The zombie's clock is ahead of the retry's, so of the two rows it is
        the one stamped later. Skew past the pending lease is what let it
        through when the fence was an ordering; the marker it lost is not."""
        org = make_org()
        user_id = new_id()
        stalled_attempt, winning_attempt = new_id(), new_id()
        winner = make_api_key(user_id, uuid4().hex)
        stalled = winner.model_copy(
            update={
                "key_hash": uuid4().hex,
                "created_at": winner.created_at + timedelta(minutes=5),
            }
        )
        marker = make_marker(stalled, stalled_attempt)
        await markers.write_record(org.id, marker)
        # The first attempt stalls before it writes anything; the retry takes
        # the marker over and lands the row and the secret the caller holds.
        taken = await markers.take_over_pending(
            org.id, user_id, marker.key, lease_bound(utcnow()), winning_attempt
        )
        assert taken is not None
        await storage.issue_api_key(
            org.id, winner, (make_key_row(org.id, winner),), winning_attempt
        )
        with pytest.raises(Conflict):
            await storage.issue_api_key(
                org.id, stalled, (make_key_row(org.id, stalled),), stalled_attempt
            )
        assert await storage.read_api_key_by_digest(winner.key_hash) == (org.id, winner)
        assert await storage.read_api_key_by_digest(stalled.key_hash) is None

    async def test_a_finished_marker_no_longer_admits_a_re_mint(
        self, storage: TenancyStorageInterface, markers: IdempotencyStorageInterface
    ) -> None:
        """The fence is the marker's liveness, not its existence: once the
        outcome is stored the secret reached the caller, and nothing re-mints
        over it."""
        org = make_org()
        user_id = new_id()
        api_key = make_api_key(user_id, uuid4().hex)
        attempt_id = new_id()
        marker = make_marker(api_key, attempt_id)
        await markers.write_record(org.id, marker)
        await storage.issue_api_key(org.id, api_key, (make_key_row(org.id, api_key),), attempt_id)
        await markers.finish_pending(org.id, user_id, marker.key, attempt_id, 201, "{}")
        rerun = api_key.model_copy(update={"key_hash": uuid4().hex, "updated_at": utcnow()})
        with pytest.raises(Conflict):
            await storage.issue_api_key(org.id, rerun, (make_key_row(org.id, rerun),), attempt_id)
        assert await storage.read_api_key_by_digest(api_key.key_hash) == (org.id, api_key)
        assert await storage.read_api_key_by_digest(rerun.key_hash) is None
