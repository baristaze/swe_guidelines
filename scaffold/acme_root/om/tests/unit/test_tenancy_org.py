"""The org delegate over the memory storage: the identity behind the
caller and its time zone, and a team org of the caller's own. The deletion
of an org and of an account have a module each, `test_org_deletion.py` and
`test_account_deletion.py`."""

import pytest

from acme.infra.impl.local import InfraLocalImpl
from acme.om.base import new_id
from acme.om.context import Role
from acme.om.exceptions import Conflict, MembershipLimitReached, NotAuthorized, ValidationFailed
from acme.om.idempotency.types.attempt import Attempt
from acme.om.tenancy import TenancyManagerInterface
from acme.om.tenancy.impl.manager import TenancyOptions
from acme.om.tenancy.storage.impl.memory import TenancyStorageMemoryImpl
from acme.om.tenancy.types.org import OrgKind

from contracts.tenancy import make_manager, request, sign_in, sign_in_with, signed_up


async def test_the_identity_behind_the_caller(manager: TenancyManagerInterface) -> None:
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    ctx = await sign_in(manager, "ann@example.test", org.id)
    identity = await manager.org.get_identity(ctx)
    assert identity.id == (await manager.members.get_user(ctx, ctx.user_id)).identity_id
    assert identity.email == "ann@example.test" and identity.operator_role is None


# A team org of one's own.


async def test_a_signed_in_person_creates_a_team_org_and_switches_to_it(
    manager: TenancyManagerInterface,
) -> None:
    dee = await signed_up(manager, "dee@example.test", "Dee")
    place = await manager.org.create_org(dee, "Dee's Bakery", None)
    assert place.org.kind is OrgKind.TEAM and place.org.personal_identity_id is None
    assert place.org.name == "Dee's Bakery" and place.org.slug.startswith("dee-s-bakery-")
    assert place.role is Role.OWNER and place.user.display_name == "Dee"
    assert place.org.created_by == place.user.id
    # The session stays where it was; the exchange is the switch.
    assert dee.org_id != place.org.id
    login = await manager.sign_in.dev_sign_in(request(), "dee@example.test")
    assert {m.org.kind for m in login.memberships} == {OrgKind.PERSONAL, OrgKind.TEAM}
    there = await sign_in_with(manager, "dee@example.test", place.org.id)
    assert there.org_id == place.org.id and there.security.role is Role.OWNER
    # A slug the person typed is kept, and a taken one is refused.
    named = await manager.org.create_org(dee, "Cafe", "cafe")
    assert named.org.slug == "cafe"
    with pytest.raises(Conflict):
        await manager.org.create_org(dee, "Cafe Two", "cafe")


@pytest.mark.parametrize(("name", "slug"), [("  ", None), ("Cafe", "Cafe"), ("Cafe", "-cafe")])
async def test_a_malformed_team_org_is_refused(
    manager: TenancyManagerInterface, storage: TenancyStorageMemoryImpl, name: str, slug: str | None
) -> None:
    dee = await signed_up(manager, "dee@example.test", "Dee")
    with pytest.raises(ValidationFailed):
        await manager.org.create_org(dee, name, slug)
    assert await storage.count_orgs() == 1


async def test_only_a_session_creates_an_org(manager: TenancyManagerInterface) -> None:
    dee = await signed_up(manager, "dee@example.test", "Dee")
    key = await manager.credentials.create_api_key(dee, "bot", Role.OWNER)
    bot = await manager.authenticate(request(), key.key)
    with pytest.raises(NotAuthorized):
        await manager.org.create_org(bot, "Bots", None)


async def test_a_rerun_of_a_create_answers_with_the_org_it_made(
    manager: TenancyManagerInterface, storage: TenancyStorageMemoryImpl
) -> None:
    dee = await signed_up(manager, "dee@example.test", "Dee")
    attempt = Attempt(target_id=new_id(), attempt_id=new_id())
    first = await manager.org.create_org(dee, "Bakery", None, attempt)
    again = await manager.org.create_org(dee, "Bakery", None, attempt)
    assert again.org.id == first.org.id == attempt.target_id
    assert again.user.id == first.user.id
    assert await storage.count_orgs() == 2


async def test_a_team_org_counts_toward_the_bound(
    storage: TenancyStorageMemoryImpl, infra: InfraLocalImpl
) -> None:
    manager = make_manager(
        storage, infra, TenancyOptions(dev_sign_in=True, max_orgs_per_identity=2)
    )
    dee = await signed_up(manager, "dee@example.test", "Dee")
    await manager.org.create_org(dee, "One", None)
    with pytest.raises(MembershipLimitReached):
        await manager.org.create_org(dee, "Two", None)


async def test_a_person_records_their_time_zone_on_their_identity(
    manager: TenancyManagerInterface,
) -> None:
    """The zone is the person's, on their identity: set by them, and refused
    when it is not an IANA name, which leaves the one they had."""
    ann, _ = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    assert (await manager.org.get_identity(ann)).time_zone is None
    identity = await manager.org.set_time_zone(ann, "Asia/Tokyo")
    assert identity.time_zone == "Asia/Tokyo"
    with pytest.raises(ValidationFailed):
        await manager.org.set_time_zone(ann, "+09:00")
    assert (await manager.org.get_identity(ann)).time_zone == "Asia/Tokyo"
