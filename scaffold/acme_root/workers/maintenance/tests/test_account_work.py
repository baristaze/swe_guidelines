"""A deleted account's work in the worker: the person gone at the identity
provider, the personal org deleted and then purged whole by the sweep, their
place in a team org gone with their account while what they made there
stays; a provider that is down, or refusing the process's key, parking the
work until it answers; and a provider that refuses the call itself failing
it at once."""

from datetime import timedelta
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest
from worker_support import build_container, request, signing, start_noop, upload

from acme.infra.buckets import Buckets
from acme.integrations.exceptions import ProviderConflict, ProviderRefused, ProviderUnavailable
from acme.integrations.identity.twin import IdentityProviderTwinImpl
from acme.om.context import Role, TenantContext
from acme.om.work.types.handler import WorkParked, WorkRefused
from acme.om.work.types.work_item import WorkItem, WorkKind
from acme.workers.maintenance.container import WorkerContainer
from acme.workers.maintenance.main import build_loop

LEASE = timedelta(seconds=30)


def identity_of(container: WorkerContainer) -> IdentityProviderTwinImpl:
    return cast(IdentityProviderTwinImpl, container.identity_provider)


async def owner_of(container: WorkerContainer, slug: str) -> TenantContext:
    ctx, _ = await container.managers.tenancy.bootstrap(
        request(), slug.title(), slug, f"owner@{slug}.test", "Owner"
    )
    return ctx


async def bob_signs_in(
    container: WorkerContainer, org_id: UUID
) -> tuple[TenantContext, TenantContext]:
    """Bob, a member of the org, signs in through the provider's twin, which
    then knows him by a subject: his session in the org, and in his personal
    org."""
    tenancy = container.managers.tenancy
    await tenancy.add_member(request(), "ajax", "bob@example.test", "Bob", Role.MEMBER)
    through = signing(container, container.identity_provider)
    places: list[TenantContext] = []
    login = await through.sign_in_with_code(
        request(), identity_of(container).issue_code("bob@example.test")
    )
    home = next(m.org.id for m in login.memberships if m.org.personal)
    for target in (org_id, home):
        code = identity_of(container).issue_code("bob@example.test")
        login = await through.sign_in_with_code(request(), code)
        identity = await tenancy.authenticate_login(request(), login.token)
        issued = await tenancy.exchange_login(identity, target)
        places.append(await tenancy.authenticate(request(), issued.token))
    return places[0], places[1]


async def claim_deletion(container: WorkerContainer) -> tuple[TenantContext, WorkItem]:
    claimed = await container.managers.work.claim(
        request(), "default", [WorkKind.DELETE_ACCOUNT], "test", LEASE
    )
    assert claimed is not None
    return claimed


async def test_a_deleted_account_leaves_nothing_of_its_person_behind(tmp_path: Path) -> None:
    container = build_container(tmp_path)
    tenancy = container.managers.tenancy
    ann = await owner_of(container, "ajax")
    bob, home = await bob_signs_in(container, ann.org_id)
    subject = (await tenancy.get_identity(bob)).subject
    assert subject is not None and subject in identity_of(container).users

    # In Ajax, a file Bob stored. At home, a file and a record of his.
    shared = await upload(container, bob, "shared.png")
    note = await upload(container, home, "note.png")
    record = await start_noop(container, home, 3)
    buckets = container.infra.get_buckets()
    assert await buckets.exists(home.org_id, Buckets.USER_FILE_UPLOADS, note.key)

    await tenancy.delete_account(bob, "bob@example.test")
    ctx, item = await claim_deletion(container)
    await build_loop(container)._handlers[item.kind].handle(ctx, item)  # pyright: ignore[reportPrivateUsage]
    await container.managers.work.complete(ctx, item)

    # The provider's side is gone.
    assert identity_of(container).deleted == [subject]
    # The personal org is deleted, and the sweep purges it whole at its next pass.
    org = await container.storage.get_tenancy_storage().read_org(home.org_id)
    assert org is not None and org.deleted_at is not None
    loop = build_loop(container)
    await loop._sweep_once()  # pyright: ignore[reportPrivateUsage]
    assert not await buckets.exists(home.org_id, Buckets.USER_FILE_UPLOADS, note.key)
    media = container.storage.get_media_storage()
    assert await media.read_file(home.org_id, note.id) is None
    orchestrations = container.storage.get_orchestrations_storage()
    assert await orchestrations.read_orchestration(home.org_id, record.id) is None
    # The next pass finds nothing left, and the sweep leaves the org out.
    await loop._sweep_once()  # pyright: ignore[reportPrivateUsage]
    service = await tenancy.service_contexts(request())
    assert home.org_id not in {ctx.org_id for ctx in service}

    # In Ajax his place is gone, and what he made stays, by id.
    assert (await media.read_file(ann.org_id, shared.id)) is not None
    assert (await container.managers.media.get_file(ann, shared.id)).created_by == bob.user_id
    with pytest.raises(Exception):  # noqa: B017 (no user by that id any more)
        await tenancy.get_user(ann, bob.user_id)


async def test_a_provider_that_is_down_parks_the_work_until_it_answers(tmp_path: Path) -> None:
    container = build_container(tmp_path)
    ann = await owner_of(container, "ajax")
    bob, home = await bob_signs_in(container, ann.org_id)
    await container.managers.tenancy.delete_account(bob, "bob@example.test")
    identity_of(container).unavailable_for = 1
    handlers = build_loop(container)._handlers  # pyright: ignore[reportPrivateUsage]
    ctx, item = await claim_deletion(container)
    with pytest.raises(WorkParked):
        await handlers[item.kind].handle(ctx, item)
    # Nothing failed and nothing moved on: the org waits for the provider.
    org = await container.storage.get_tenancy_storage().read_org(home.org_id)
    assert org is not None and org.deleted_at is None
    await handlers[item.kind].handle(ctx, item)
    assert len(identity_of(container).deleted) == 1
    org = await container.storage.get_tenancy_storage().read_org(home.org_id)
    assert org is not None and org.deleted_at is not None
    # A second run finds every step done.
    await handlers[item.kind].handle(ctx, item)
    assert len(identity_of(container).deleted) == 1


@pytest.mark.parametrize(
    ("error", "outcome"),
    [
        # What the WorkOS client raises for a 4xx on the request (400, 404
        # past the one that means gone, 422), and for a 5xx or a 401/403 on
        # its own key, which it answers as unavailable.
        (ProviderRefused("deleting the user: bad id"), WorkRefused),
        (ProviderConflict("deleting the user: in the way"), WorkRefused),
        (ProviderUnavailable("deleting the user: WorkOS answered 503"), WorkParked),
        (ProviderUnavailable("deleting the user: WorkOS refused the key (401)"), WorkParked),
    ],
)
async def test_a_refusal_of_the_call_fails_and_one_that_may_pass_parks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    outcome: type[Exception],
) -> None:
    container = build_container(tmp_path)
    ann = await owner_of(container, "ajax")
    bob, home = await bob_signs_in(container, ann.org_id)
    await container.managers.tenancy.delete_account(bob, "bob@example.test")
    ctx, item = await claim_deletion(container)

    async def answer(user_id: str) -> None:
        raise error

    monkeypatch.setattr(identity_of(container), "delete_user", answer)
    with pytest.raises(outcome) as raised:
        await build_loop(container)._handlers[item.kind].handle(ctx, item)  # pyright: ignore[reportPrivateUsage]
    assert str(error) in str(raised.value)
    # Either way nothing moved on: the org waits, for the provider or a person.
    org = await container.storage.get_tenancy_storage().read_org(home.org_id)
    assert org is not None and org.deleted_at is None
