"""The container builds every root once, at boot; a request constructs
nothing (ADR 0007)."""

from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from acme.infra.impl.local import InfraLocalImpl
from acme.om.storage.impl.memory import StorageMemoryImpl
from acme.services.api import container as container_module
from acme.services.api.app import create_app
from acme.services.api.container import AppContainer
from acme.services.api.main import main


async def test_managers_are_built_once_for_any_number_of_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []
    real_build = container_module.build_managers

    def counting_build(*args: object, **kwargs: object) -> object:
        calls.append(1)
        return real_build(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(container_module, "build_managers", counting_build)
    container = AppContainer.for_tests(StorageMemoryImpl(), InfraLocalImpl(tmp_path))
    assert len(calls) == 1

    app: FastAPI = create_app(container)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # Flows that touch different managers, or none: the health probe,
            # readiness (storage), a sign-in attempt (tenancy), a task list
            # (the gateway refuses before tasks), events.
            for path in ("/healthz", "/readyz", "/v1/tasks", "/v1/events", "/metrics"):
                await client.get(path)
            await client.post("/v1/auth/dev-sign-in", json={"email": "nobody@example.test"})
    assert len(calls) == 1
    # Every router resolves the same frozen object the container built.
    assert app.state.container.managers is container.managers


@pytest.mark.parametrize("role", ["owner", "service"])
def test_add_member_refuses_a_role_no_membership_can_take(
    role: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """The ops CLI's --role choices are the roles `update_membership_role`
    accepts. OWNER is the org's own, minted by bootstrap, and SERVICE is the
    role a sweep's context carries, which the manager refuses outright: either
    one offered here would fail with an uncaught ValidationFailed instead of a
    usage message."""
    with pytest.raises(SystemExit) as exit_code:
        main(
            [
                "add-member",
                "--slug",
                "ajax",
                "--email",
                "a@b.test",
                "--name",
                "A",
                "--role",
                role,
            ]
        )
    assert exit_code.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_the_test_container_never_reads_the_developers_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`make up` copies .env.example to .env with the local tracker's DSN in
    it; a test container that read it would boot error reporting and send
    every error a test raises on purpose to that tracker."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "ACME_SENTRY_DSN=http://key@127.0.0.1:1/1\nACME_OTEL_ENDPOINT=http://127.0.0.1:1\n"
    )
    built = AppContainer.for_tests(StorageMemoryImpl(), InfraLocalImpl(tmp_path))
    assert built.settings.sentry_dsn is None
    assert built.settings.otel_endpoint is None
