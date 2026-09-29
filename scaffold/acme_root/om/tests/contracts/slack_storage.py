"""The slack storage contract: the installation is unique among the living
per org and per workspace, a refresh is claimed by one caller at a time, an
install state is redeemed once and never after it expired, a post is recorded
once per key, and every read and write is fenced by the tenant. The cases
named in `CROSS_TENANT_CASES` present another tenant's identifier and assert
that nothing is found and nothing changes."""

from datetime import datetime, timedelta
from uuid import UUID

import pytest

from contracts.racing import race
from acme.om.base import new_id, utcnow
from acme.om.exceptions import TenantMismatch, UniqueKeyTaken
from acme.om.slack.rules import credential_ref_for, new_state, state_digest
from acme.om.slack.storage import SlackStorageInterface
from acme.om.slack.types.installation import (
    SlackInstallation,
    SlackInstallationStatus,
    SlackInstallState,
    SlackPost,
)

CROSS_TENANT_CASES: frozenset[str] = frozenset(
    {
        "claim_refresh",
        "create_install_state",
        "create_post",
        "purge_tenant",
        "read_installation",
        "read_post",
        "settle_refresh",
        "write_installation",
    }
)
"""Every method of `SlackStorageInterface` that takes a tenant has a case in
this module that presents another tenant's."""


def make_installation(team_id: str = "T1") -> SlackInstallation:
    now = utcnow()
    user = new_id()
    installation_id = new_id()
    return SlackInstallation(
        id=installation_id,
        created_at=now,
        updated_at=now,
        created_by=user,
        updated_by=user,
        team_id=team_id,
        team_name="Ajax",
        app_id="A1",
        bot_user_id="UBOT",
        scopes="commands,chat:write",
        installed_by_slack_user="U1",
        credential_ref=credential_ref_for(installation_id),
        token_expires_at=now + timedelta(hours=12),
    )


def make_state(state: str, *, lifetime: timedelta = timedelta(minutes=10)) -> SlackInstallState:
    now = utcnow()
    return SlackInstallState(
        id=new_id(),
        created_at=now,
        user_id=new_id(),
        state_hash=state_digest(state),
        expires_at=now + lifetime,
    )


def make_post(key: UUID) -> SlackPost:
    return SlackPost(id=new_id(), created_at=utcnow(), key=key, channel_id="C1", ts="1.2")


def posted_at(post: SlackPost, at: datetime) -> SlackPost:
    return post.model_copy(update={"created_at": at})


async def drained(storage: SlackStorageInterface) -> datetime:
    """The moment a purge case stands at: a century back, so no other case's
    row is past its cut, with whatever an earlier run of these cases left
    behind it purged first. The purge reaches across tenants, so a case owns
    the rows behind its cut."""
    then = utcnow() - timedelta(days=36500)
    while await storage.purge(then + timedelta(hours=1), 1000):
        pass
    return then


class SlackStorageContract:
    @pytest.fixture
    def storage(self) -> SlackStorageInterface:
        raise NotImplementedError("the concrete test class provides the storage")

    async def test_an_installation_round_trips_and_is_found_by_its_workspace(
        self, storage: SlackStorageInterface
    ) -> None:
        org = new_id()
        installation = make_installation()
        await storage.write_installation(org, installation, ())
        assert await storage.read_installation(org) == installation
        assert await storage.read_installation_by_team("T1") == (org, installation)
        assert await storage.read_installation_by_team("T2") is None

    async def test_reads_and_writes_are_fenced_by_the_tenant(
        self, storage: SlackStorageInterface
    ) -> None:
        org, other = new_id(), new_id()
        installation = make_installation()
        await storage.write_installation(org, installation, ())
        assert await storage.read_installation(other) is None
        broken = installation.model_copy(update={"status": SlackInstallationStatus.BROKEN})
        with pytest.raises(TenantMismatch):
            await storage.write_installation(other, broken, ())
        now = utcnow()
        assert await storage.claim_refresh(other, installation.id, now, now) is False
        await storage.settle_refresh(other, installation.id, None, now)
        assert await storage.read_installation(org) == installation
        await storage.create_post(org, make_post(key := new_id()))
        assert await storage.read_post(other, key) is None
        assert await storage.create_post(other, make_post(key)) is True, "a key is per tenant"
        await storage.create_install_state(other, make_state(new_state()))

    async def test_an_org_has_one_living_installation_and_a_workspace_one_org(
        self, storage: SlackStorageInterface
    ) -> None:
        org, other = new_id(), new_id()
        first = make_installation("T1")
        await storage.write_installation(org, first, ())
        with pytest.raises(UniqueKeyTaken):
            await storage.write_installation(org, make_installation("T2"), ())
        with pytest.raises(UniqueKeyTaken):
            await storage.write_installation(other, make_installation("T1"), ())
        assert await storage.read_installation(other) is None
        # Deleting frees both keys: the org installs again, and so does the
        # workspace, for another org.
        now = utcnow()
        gone = first.model_copy(update={"deleted_at": now, "deleted_by": first.created_by})
        await storage.write_installation(org, gone, ())
        assert await storage.read_installation(org) is None
        again = make_installation("T1")
        await storage.write_installation(other, again, ())
        assert await storage.read_installation_by_team("T1") == (other, again)
        await storage.write_installation(org, make_installation("T2"), ())

    async def test_one_caller_holds_a_refresh_until_it_settles_or_lapses(
        self, storage: SlackStorageInterface
    ) -> None:
        org = new_id()
        installation = make_installation()
        await storage.write_installation(org, installation, ())
        now = utcnow()
        until = now + timedelta(seconds=30)
        run = await race(
            *(storage.claim_refresh(org, installation.id, now, until) for _ in range(3))
        )
        assert run.outcomes.count(True) == 1, run.summary()
        assert await storage.claim_refresh(org, installation.id, now, until) is False
        # A claim that lapsed is taken over.
        assert await storage.claim_refresh(org, installation.id, until, until) is True
        later = now + timedelta(hours=24)
        await storage.settle_refresh(org, installation.id, later, now)
        stored = await storage.read_installation(org)
        assert stored is not None
        assert stored.token_expires_at == later and stored.refreshing_until is None
        assert await storage.claim_refresh(org, installation.id, now, until) is True

    async def test_a_state_is_redeemed_once(self, storage: SlackStorageInterface) -> None:
        org = new_id()
        state = new_state()
        started = make_state(state)
        await storage.create_install_state(org, started)
        now = utcnow()
        redeemed = await storage.redeem_install_state(state_digest(state), now)
        assert redeemed is not None
        assert redeemed[0] == org and redeemed[1].id == started.id
        assert redeemed[1].redeemed_at == now
        assert await storage.redeem_install_state(state_digest(state), now) is None
        assert await storage.redeem_install_state(state_digest(new_state()), now) is None

    async def test_two_redemptions_race_and_one_wins(self, storage: SlackStorageInterface) -> None:
        state = new_state()
        await storage.create_install_state(new_id(), make_state(state))
        now = utcnow()
        run = await race(
            *(storage.redeem_install_state(state_digest(state), now) for _ in range(3))
        )
        assert len(run.admitted) == 1, run.summary()
        assert await storage.redeem_install_state(state_digest(state), now) is None

    async def test_an_expired_state_is_refused(self, storage: SlackStorageInterface) -> None:
        state = new_state()
        await storage.create_install_state(
            new_id(), make_state(state, lifetime=timedelta(seconds=-1))
        )
        assert await storage.redeem_install_state(state_digest(state), utcnow()) is None

    async def test_a_state_digest_is_unique(self, storage: SlackStorageInterface) -> None:
        state = new_state()
        await storage.create_install_state(new_id(), make_state(state))
        with pytest.raises(UniqueKeyTaken):
            await storage.create_install_state(new_id(), make_state(state))

    async def test_a_post_is_recorded_once_per_key(self, storage: SlackStorageInterface) -> None:
        org, key = new_id(), new_id()
        first = make_post(key)
        assert await storage.create_post(org, first) is True
        assert await storage.create_post(org, make_post(key)) is False
        assert await storage.read_post(org, key) == first

    async def test_the_purge_takes_what_is_past_the_retention(
        self, storage: SlackStorageInterface
    ) -> None:
        """The purge runs across tenants: every tenant's rows past the cut go
        in one call, and a tenant's purge takes its rows and no other's."""
        org, other = new_id(), new_id()
        then = await drained(storage)
        installation = make_installation()
        await storage.write_installation(
            org, installation.model_copy(update={"deleted_at": then, "deleted_by": new_id()}), ()
        )
        await storage.create_install_state(
            org, make_state(new_state(), lifetime=then - utcnow() + timedelta(minutes=10))
        )
        await storage.create_post(org, posted_at(make_post(new_id()), then))
        await storage.create_post(other, posted_at(make_post(new_id()), then))
        assert await storage.purge(then - timedelta(days=1), 10) == 0
        assert await storage.purge(then + timedelta(hours=1), 10) == 4, "every tenant's"
        await storage.write_installation(org, make_installation(), ())
        await storage.create_post(other, make_post(new_id()))
        assert await storage.purge_tenant(org, 10) == 1
        assert await storage.read_installation(org) is None
        assert await storage.purge_tenant(other, 10) == 1

    async def test_a_backlog_past_a_batch_goes_a_batch_at_a_time(
        self, storage: SlackStorageInterface
    ) -> None:
        org = new_id()
        then = await drained(storage)
        for _ in range(3):
            await storage.create_post(org, posted_at(make_post(new_id()), then))
        later = then + timedelta(hours=1)
        assert await storage.purge(later, 2) == 2
        assert await storage.purge(later, 2) == 1
        assert await storage.purge(later, 2) == 0
        for _ in range(3):
            await storage.create_post(org, make_post(new_id()))
        assert await storage.purge_tenant(org, 2) == 2
        assert await storage.purge_tenant(org, 2) == 1
