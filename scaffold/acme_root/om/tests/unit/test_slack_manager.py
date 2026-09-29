"""The slack manager over the memory roots and Slack's twin: an install is
bound to the org and the person who started it, works once and only in time,
keeps the token as the org's own secret and nowhere else, and gives a
workspace to one org; an uninstall takes the token away; a channel that
broke the install mends when the bot joins it again; the token renews
before it expires, one renewal at a time; and a Slack user is matched to a
member by the address their profile holds."""

from collections import Counter
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from acme.infra.exceptions import SecretNotFound
from acme.infra.impl.local import InfraLocalImpl
from acme.integrations.identity.absent import IdentityProviderAbsentImpl
from acme.integrations.impl.configured import IntegrationsOverImpl
from acme.integrations.slack import SlackTokenRevoked
from acme.integrations.slack.twin import SlackTwinImpl
from acme.om.base import new_id, utcnow
from acme.om.exceptions import NotAuthorized, NotFound, SlackWorkspaceTaken
from acme.om.opcontext import AppContext, AppType, OpContext, RequestContext, Role
from acme.om.root import Managers, build_managers
from acme.om.slack.impl.manager import tokens_from, tokens_json
from acme.om.slack.types.installation import SlackInstallationStatus
from acme.om.storage.impl.memory import StorageMemoryImpl
from acme.om.tenancy.impl.manager import TenancyOptions

APP = AppContext(type=AppType.PORTAL, version="portal@test")
REDIRECT = "https://api.acme.test/webhooks/slack/oauth"


def request() -> RequestContext:
    return RequestContext(request_id=new_id(), app=APP)


class World:
    def __init__(self, tmp_path: Path) -> None:
        self.storage = StorageMemoryImpl()
        self.infra = InfraLocalImpl(tmp_path)
        self.twin = SlackTwinImpl("test")
        self.managers: Managers = build_managers(
            self.storage,
            self.infra,
            TenancyOptions(dev_sign_in=True),
            integrations=IntegrationsOverImpl(IdentityProviderAbsentImpl(), slack=self.twin),
        )

    async def org(self, slug: str) -> OpContext:
        ctx, _ = await self.managers.tenancy.bootstrap(
            request(), slug.title(), slug, f"owner@{slug}.test", "Owner"
        )
        return ctx

    async def member(self, slug: str, email: str, role: Role = Role.MEMBER) -> OpContext:
        tenancy = self.managers.tenancy
        await tenancy.add_member(request(), slug, email, "Member", role)
        login = await tenancy.dev_sign_in(request(), email)
        identity = await tenancy.authenticate_login(request(), login.token)
        memberships = await tenancy.get_identity_memberships(identity, None, 10)
        org = next(m.org.id for m in memberships.items if m.org.slug == slug)
        session = await tenancy.exchange_login(identity, org)
        return await tenancy.authenticate(request(), session.token)

    async def install(self, ctx: OpContext, team: str = "T0AJAX") -> str:
        """An owner starts the install, and Slack sends the browser back with
        a code for `team`: answers the state it carried."""
        start = await self.managers.slack.start_install(ctx, REDIRECT)
        state = parse_qs(urlsplit(start.url).query)["state"][0]
        code = self.twin.approve(team, "U0OWNER", "Ajax")
        await self.managers.slack.finish_install(request(), state, code, REDIRECT)
        return state


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


async def test_an_install_is_the_orgs_and_its_token_is_the_orgs_secret(world: World) -> None:
    owner = await world.org("ajax")
    await world.install(owner)
    installation = await world.managers.slack.get_installation(owner)
    assert installation is not None
    assert installation.team_id == "T0AJAX" and installation.created_by == owner.user_id
    assert installation.installed_by_slack_user == "U0OWNER"
    assert installation.channel_id is None
    assert installation.token_expires_at is not None
    stored = tokens_from(
        await world.infra.get_secrets().get(owner.org_id, installation.credential_ref)
    )
    assert world.twin.team_of(stored.access_token.get_secret_value()) == "T0AJAX"
    assert "xoxe" not in installation.model_dump_json(), "no token on the row"
    # Another org's name for the secret reads nothing.
    other = await world.org("fabrikam")
    with pytest.raises(SecretNotFound):
        await world.infra.get_secrets().get(other.org_id, installation.credential_ref)


async def test_only_an_owner_or_an_admin_starts_an_install(world: World) -> None:
    await world.org("ajax")
    bob = await world.member("ajax", "bob@ajax.test")
    with pytest.raises(NotAuthorized):
        await world.managers.slack.start_install(bob, REDIRECT)


async def test_a_state_works_once_in_time_and_only_as_issued(world: World) -> None:
    owner = await world.org("ajax")
    slack = world.managers.slack
    state = await world.install(owner)
    with pytest.raises(NotFound):  # reused
        await slack.finish_install(request(), state, world.twin.approve("T0AJAX", "U1"), REDIRECT)
    with pytest.raises(NotFound):  # forged
        await slack.finish_install(
            request(), "not-a-state", world.twin.approve("T0AJAX", "U1"), REDIRECT
        )
    start = await slack.start_install(owner, REDIRECT)
    late = parse_qs(urlsplit(start.url).query)["state"][0]
    stored = world.storage.get_slack_storage()
    for org_id, row in list(stored._states.values()):  # type: ignore[attr-defined]
        stored._states[row.id] = (  # type: ignore[attr-defined]
            org_id,
            row.model_copy(update={"expires_at": utcnow() - timedelta(seconds=1)}),
        )
    with pytest.raises(NotFound):  # expired
        await slack.finish_install(request(), late, world.twin.approve("T0AJAX", "U1"), REDIRECT)


async def test_a_workspace_is_one_orgs_and_the_second_token_is_revoked(world: World) -> None:
    ajax = await world.org("ajax")
    fabrikam = await world.org("fabrikam")
    await world.install(ajax, "T0SHARED")
    start = await world.managers.slack.start_install(fabrikam, REDIRECT)
    state = parse_qs(urlsplit(start.url).query)["state"][0]
    code = world.twin.approve("T0SHARED", "U0OTHER")
    grant_token = world.twin._codes[code].tokens.access_token.get_secret_value()  # type: ignore[attr-defined]
    with pytest.raises(SlackWorkspaceTaken):
        await world.managers.slack.finish_install(request(), state, code, REDIRECT)
    assert await world.managers.slack.get_installation(fabrikam) is None
    assert world.twin.team_of(grant_token) is None, "the token Acme will not keep is revoked"
    assert await world.managers.slack.bot_token(ajax), "the org holding it keeps working"


async def test_installing_again_keeps_the_channel_and_mends_the_install(world: World) -> None:
    owner = await world.org("ajax")
    slack = world.managers.slack
    await world.install(owner)
    await slack.bind_channel(owner, "C0TEAM")
    await slack.mark_broken(owner, "not_in_channel")
    before = await slack.get_installation(owner)
    await world.install(owner)
    after = await slack.get_installation(owner)
    assert before is not None and after is not None
    assert after.id == before.id and after.channel_id == "C0TEAM"
    assert after.status is SlackInstallationStatus.OK and after.broken_reason is None


@pytest.mark.parametrize("reason", ["not_in_channel", "channel_not_found", "is_archived"])
async def test_the_bots_join_to_its_channel_mends_what_the_channel_broke(
    world: World, reason: str
) -> None:
    owner = await world.org("ajax")
    slack = world.managers.slack
    await world.install(owner)
    await slack.bind_channel(owner, "C0TEAM")
    await slack.mark_broken(owner, reason)
    assert await slack.bot_joined(owner, "C0ELSEWHERE") is None, "another channel mends nothing"
    mended = await slack.bot_joined(owner, "C0TEAM")
    assert mended is not None and mended.channel_id == "C0TEAM"
    assert mended.status is SlackInstallationStatus.OK and mended.broken_reason is None
    assert await slack.get_installation(owner) == mended
    assert await slack.bot_joined(owner, "C0TEAM") is None, "a second join finds it well"


async def test_the_bots_join_leaves_a_refused_token_broken(world: World) -> None:
    owner = await world.org("ajax")
    slack = world.managers.slack
    assert await slack.bot_joined(owner, "C0TEAM") is None, "no installation"
    await world.install(owner)
    await slack.bind_channel(owner, "C0TEAM")
    await slack.mark_broken(owner, "invalid_refresh_token")
    assert await slack.bot_joined(owner, "C0TEAM") is None
    installation = await slack.get_installation(owner)
    assert installation is not None and installation.status is SlackInstallationStatus.BROKEN
    assert installation.broken_reason == "invalid_refresh_token", "only a new install mends it"


async def test_another_workspace_replaces_the_first_and_the_app_leaves_it(world: World) -> None:
    owner = await world.org("ajax")
    await world.install(owner, "T0FIRST")
    first = await world.managers.slack.get_installation(owner)
    await world.install(owner, "T0SECOND")
    second = await world.managers.slack.get_installation(owner)
    assert first is not None and second is not None and second.team_id == "T0SECOND"
    assert world.twin.uninstalled == ["T0FIRST"]
    assert not await world.infra.get_secrets().has(owner.org_id, first.credential_ref)


async def test_an_uninstall_removes_the_app_and_the_token(world: World) -> None:
    owner = await world.org("ajax")
    await world.install(owner)
    installation = await world.managers.slack.get_installation(owner)
    assert installation is not None
    gone = await world.managers.slack.uninstall(owner)
    assert gone is not None and gone.deleted_at is not None
    assert world.twin.uninstalled == ["T0AJAX"]
    assert await world.managers.slack.get_installation(owner) is None
    assert not await world.infra.get_secrets().has(owner.org_id, installation.credential_ref)
    assert await world.managers.slack.uninstall(owner) is None


async def test_forgetting_leaves_slack_alone(world: World) -> None:
    owner = await world.org("ajax")
    await world.install(owner)
    assert await world.managers.slack.forget(owner, "app_uninstalled") is not None
    assert world.twin.uninstalled == []
    assert await world.managers.slack.get_installation(owner) is None


async def test_a_token_renews_before_it_expires_one_renewal_at_a_time(world: World) -> None:
    owner = await world.org("ajax")
    slack = world.managers.slack
    await world.install(owner)
    first = await slack.bot_token(owner)
    assert await slack.bot_token(owner) == first, "a fresh token is used as it is"
    installation = await slack.get_installation(owner)
    assert installation is not None
    secrets = world.infra.get_secrets()
    stored = tokens_from(await secrets.get(owner.org_id, installation.credential_ref))
    soon = stored.model_copy(update={"expires_at": utcnow() + timedelta(minutes=5)})
    await secrets.put(owner.org_id, installation.credential_ref, tokens_json(soon))
    # Another caller holds the renewal: this one keeps the token that still works.
    storage = world.storage.get_slack_storage()
    now = utcnow()
    assert await storage.claim_refresh(owner.org_id, installation.id, now, now + timedelta(30))
    assert await slack.bot_token(owner) == first
    await storage.settle_refresh(owner.org_id, installation.id, soon.expires_at, now)
    renewed = await slack.bot_token(owner)
    assert renewed != first and world.twin.team_of(renewed) == "T0AJAX"
    after = await slack.get_installation(owner)
    assert after is not None and after.refreshing_until is None
    assert after.token_expires_at is not None and after.token_expires_at > now + timedelta(hours=11)
    # The refresh token it used works once.
    with pytest.raises(SlackTokenRevoked):
        await world.twin.refresh(stored.refresh_token.get_secret_value())  # type: ignore[union-attr]


async def test_a_token_that_no_longer_renews_breaks_the_install(world: World) -> None:
    owner = await world.org("ajax")
    slack = world.managers.slack
    await world.install(owner)
    installation = await slack.get_installation(owner)
    assert installation is not None
    secrets = world.infra.get_secrets()
    stored = tokens_from(await secrets.get(owner.org_id, installation.credential_ref))
    await secrets.put(
        owner.org_id,
        installation.credential_ref,
        tokens_json(stored.model_copy(update={"expires_at": utcnow()})),
    )
    world.twin.revoke_team("T0AJAX")
    with pytest.raises(SlackTokenRevoked):
        await slack.bot_token(owner)
    broken = await slack.get_installation(owner)
    assert broken is not None and broken.status is SlackInstallationStatus.BROKEN
    assert broken.broken_reason == "invalid_refresh_token"


def counted_secrets(world: World, monkeypatch: pytest.MonkeyPatch) -> Counter[str]:
    """How many times the org's secrets were asked, by call."""
    secrets = world.infra.get_secrets()
    calls: Counter[str] = Counter()
    for name in ("get", "has"):
        real = getattr(secrets, name)

        async def call(*args: Any, _real: Any = real, _name: str = name, **kw: Any) -> Any:
            calls[_name] += 1
            return await _real(*args, **kw)

        monkeypatch.setattr(secrets, name, call)
    return calls


async def test_a_bot_token_is_one_read_of_the_orgs_secrets(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await world.org("ajax")
    await world.install(owner)
    calls = counted_secrets(world, monkeypatch)
    assert await world.managers.slack.bot_token(owner)
    assert calls == {"get": 1}


async def test_a_token_gone_from_the_secrets_breaks_the_install(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await world.org("ajax")
    slack = world.managers.slack
    await world.install(owner)
    installation = await slack.get_installation(owner)
    assert installation is not None
    await world.infra.get_secrets().delete(owner.org_id, installation.credential_ref)
    calls = counted_secrets(world, monkeypatch)
    with pytest.raises(SlackTokenRevoked, match="token_missing"):
        await slack.bot_token(owner)
    assert calls == {"get": 1}
    broken = await slack.get_installation(owner)
    assert broken is not None and broken.status is SlackInstallationStatus.BROKEN
    assert broken.broken_reason == "token_missing"
    # An uninstall with no token leaves Slack alone and still removes the row.
    calls.clear()
    assert await slack.uninstall(owner) is not None
    assert calls == {"get": 1}
    assert world.twin.uninstalled == []
    assert await slack.get_installation(owner) is None


async def test_a_slack_user_is_matched_to_a_member_by_address(world: World) -> None:
    owner = await world.org("ajax")
    await world.org("fabrikam")
    bob = await world.member("ajax", "bob@ajax.test")
    tenancy = world.managers.tenancy
    found = await tenancy.member_context(request(), owner.org_id, "Bob@Ajax.test")
    assert found is not None and found.user_id == bob.user_id and found.role is Role.MEMBER
    owner_found = await tenancy.member_context(request(), owner.org_id, "owner@ajax.test")
    assert owner_found is not None and owner_found.role is Role.OWNER
    assert await tenancy.member_context(request(), owner.org_id, "nobody@ajax.test") is None
    # A person of another org is nobody here.
    assert await tenancy.member_context(request(), owner.org_id, "owner@fabrikam.test") is None
    # Added as `Dee@Ajax.test`, spelled `DEE@ajax.TEST` on the profile: one address.
    dee = await world.member("ajax", "Dee@Ajax.test")
    dee_found = await tenancy.member_context(request(), owner.org_id, "DEE@ajax.TEST")
    assert dee_found is not None and dee_found.user_id == dee.user_id
