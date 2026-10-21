"""The local mint, `acme-api mint-local-login`: a login for each address on
a local database, for the machines that drive a local stack with no browser
or mailbox. It is a command and never a route, and it refuses a deployed
environment before it builds or writes anything."""

import os
from pathlib import Path

import pytest
from api_support import OWNER, bearer, build_container, client_over, run, seed_request
from pydantic import ValidationError

from acme.infra.impl.local import InfraLocalImpl
from acme.om.context import Role
from acme.om.exceptions import NotFound, ValidationFailed
from acme.om.storage.impl.memory import StorageMemoryImpl
from acme.services.api import main as api_main
from acme.services.api.container import AppContainer
from acme.services.api.settings import ApiSettings

MEMBER = "bob@example.test"


@pytest.fixture
def clean_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A working directory with no `.env`, and no ACME_ setting exported, so
    the command reads only what a test sets."""
    monkeypatch.chdir(tmp_path)
    for name in [n for n in os.environ if n.startswith("ACME_")]:
        monkeypatch.delenv(name)
    return tmp_path


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_the_mint_refuses_a_deployed_environment_and_writes_nothing(
    clean_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    environment: str,
) -> None:
    monkeypatch.setenv("ACME_ENVIRONMENT", environment)
    built: list[ApiSettings] = []

    def build(settings: ApiSettings) -> AppContainer:
        built.append(settings)
        raise AssertionError("the mint built a container")

    monkeypatch.setattr(AppContainer, "build", staticmethod(build))
    code = api_main.main(["mint-local-login", "--email", OWNER["email"]])
    out, err = capsys.readouterr()
    assert code == 2 and out == "" and built == []
    assert f"ACME_LOCAL_LOGIN_MINT=true is refused when ACME_ENVIRONMENT={environment}" in err


@pytest.mark.parametrize("environment", ["dev", "staging", "production"])
def test_the_settings_refuse_the_mint_outside_local_and_test(environment: str) -> None:
    with pytest.raises(ValidationError, match="ACME_LOCAL_LOGIN_MINT"):
        ApiSettings.model_validate(
            {
                "_env_file": None,
                "environment": environment,
                "local_login_mint": True,
                "sign_in_redirect_uris": ["https://app.example.test/auth/callback"],
                "sign_out_return_uris": ["https://app.example.test/signed-out"],
            }
        )


def test_the_printed_logins_exchange_for_sessions_in_a_seeded_org(
    clean_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """What the traffic generator reads: one login a line, in the order the
    addresses were given, each exchanged for a session in the seeded org."""
    monkeypatch.setenv("ACME_ENVIRONMENT", "test")
    storage = StorageMemoryImpl()
    seeding = build_container(clean_env, storage)
    _, org = run(
        seeding.managers.tenancy.bootstrap(
            seed_request(), "Ajax", "ajax", OWNER["email"], OWNER["name"]
        )
    )
    run(seeding.managers.tenancy.add_member(seed_request(), "ajax", MEMBER, "Bob", Role.MEMBER))

    def build(settings: ApiSettings) -> AppContainer:
        assert settings.local_login_mint
        return AppContainer.for_tests(storage, InfraLocalImpl(clean_env), settings)

    monkeypatch.setattr(AppContainer, "build", staticmethod(build))
    code = api_main.main(["mint-local-login", "--email", OWNER["email"], "--email", MEMBER])
    out, _ = capsys.readouterr()
    tokens = out.splitlines()
    assert code == 0 and len(tokens) == 2 and all(t.startswith("lgn_") for t in tokens)

    async def exchanged() -> list[str]:
        async with client_over(build_container(clean_env, storage)) as client:
            roles: list[str] = []
            for token in tokens:
                session = await client.post(
                    "/v1/auth/sessions", json={"org_id": str(org.id)}, headers=bearer(token)
                )
                assert session.status_code == 200, session.text
                me = await client.get("/v1/me", headers=bearer(session.json()["token"]))
                roles.append(f"{me.json()['user']['email']} {me.json()['role']}")
                # A minted login left no session at the provider to end.
                out = await client.post("/v1/auth/logout", headers=bearer(session.json()["token"]))
                assert out.status_code == 200 and out.json()["provider_logout_url"] is None
            return roles

    assert run(exchanged()) == [f"{OWNER['email']} owner", f"{MEMBER} member"]


async def test_the_mint_makes_a_person_nobody_knew_and_refuses_the_systems_own(
    tmp_path: Path,
) -> None:
    container = build_container(tmp_path, local_login_mint=True)
    sign_in = container.managers.tenancy.sign_in
    first = await sign_in.mint_local_login(seed_request(), "dee@example.test", "Dee")
    [place] = first.memberships
    assert place.org.personal and place.org.name == "Dee"
    again = await sign_in.mint_local_login(seed_request(), "dee@example.test")
    assert [m.org.id for m in again.memberships] == [place.org.id]
    with pytest.raises(ValidationFailed):
        await sign_in.mint_local_login(seed_request(), "smoke@platform.acme.invalid")


async def test_the_mint_is_off_in_every_process_that_does_not_turn_it_on(
    tmp_path: Path,
) -> None:
    container = build_container(tmp_path)
    with pytest.raises(NotFound):
        await container.managers.tenancy.sign_in.mint_local_login(seed_request(), "d@example.test")
    async with client_over(container) as client:
        door = await client.post("/v1/auth/dev-sign-in", json={"email": "d@example.test"})
    assert door.status_code == 404
