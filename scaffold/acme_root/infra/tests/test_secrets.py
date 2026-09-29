from pathlib import Path

import pytest

from acme.infra.base import new_id
from acme.infra.impl.settings import InfraSettings
from acme.infra.secrets import InvalidSecretName, SecretNotFound, SecretsFileNotPrivate
from acme.infra.secrets.local import SecretsLocalImpl

ORG = new_id()


async def test_boot_time_overrides_win_over_the_file(tmp_path: Path) -> None:
    secrets = SecretsLocalImpl(tmp_path / "secrets.env")
    await secrets.put(ORG, "db_password", "from-file")
    assert await secrets.get(ORG, "db_password") == "from-file"
    overridden = SecretsLocalImpl(
        tmp_path / "secrets.env", {f"{ORG.hex}_DB_PASSWORD".upper(): "from-env"}
    )
    assert await overridden.get(ORG, "db_password") == "from-env"
    assert await overridden.has(ORG, "db_password")


def test_settings_collect_the_overrides_once_at_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACME_SECRET_DB_PASSWORD", "from-env")
    monkeypatch.setenv("ACME_SECRET_", "ignored: no name")
    settings = InfraSettings.model_validate({"environment": "test"})
    assert settings.secret_overrides == {"DB_PASSWORD": "from-env"}
    monkeypatch.setenv("ACME_SECRET_LATER", "set after boot")
    assert "LATER" not in settings.secret_overrides
    assert "secret_overrides" not in settings.model_dump()


def test_the_overrides_come_from_dot_env_too_and_the_environment_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`.env.example` documents ACME_SECRET_<NAME> as a `.env` knob, so it has
    to work like one. Pydantic's dotenv source cannot supply these — one key
    per secret is not a declared field — so the file is read here; the process
    environment still wins, the way it does for every other setting."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "ACME_SECRET_DB_PASSWORD=from-the-file  # with a comment\n"
        "ACME_SECRET_API_TOKEN=only-in-the-file\n"
    )
    monkeypatch.setenv("ACME_SECRET_DB_PASSWORD", "from-the-environment")
    settings = InfraSettings.model_validate({"environment": "test"})
    assert settings.secret_overrides == {
        "DB_PASSWORD": "from-the-environment",
        "API_TOKEN": "only-in-the-file",
    }


async def test_missing_secret_names_the_store_not_a_value(tmp_path: Path) -> None:
    secrets = SecretsLocalImpl(tmp_path / "secrets.env")
    assert not await secrets.has(ORG, "nothing")
    with pytest.raises(SecretNotFound) as raised:
        await secrets.get(ORG, "nothing")
    assert "local" in raised.value.message


async def test_file_must_be_owner_only(tmp_path: Path) -> None:
    file = tmp_path / "secrets.env"
    file.write_text(f"org/{ORG}/token=abc\n")
    file.chmod(0o644)
    with pytest.raises(SecretsFileNotPrivate):
        await SecretsLocalImpl(file).get(ORG, "token")
    file.chmod(0o600)
    assert await SecretsLocalImpl(file).get(ORG, "token") == "abc"


async def test_delete_removes_from_the_file(tmp_path: Path) -> None:
    secrets = SecretsLocalImpl(tmp_path / "secrets.env")
    await secrets.put(ORG, "a", "1")
    await secrets.put(ORG, "b", "2")
    await secrets.delete(ORG, "a")
    assert not await secrets.has(ORG, "a")
    assert await secrets.get(ORG, "b") == "2"


async def test_a_tenant_never_resolves_another_tenants_secret(tmp_path: Path) -> None:
    secrets = SecretsLocalImpl(tmp_path / "secrets.env")
    other = new_id()
    await secrets.put(ORG, "carrier_key", "ours")
    assert not await secrets.has(other, "carrier_key")
    with pytest.raises(SecretNotFound):
        await secrets.get(other, "carrier_key")
    # Nor does an override set for one tenant.
    overridden = SecretsLocalImpl(None, {f"{ORG.hex}_CARRIER_KEY".upper(): "ours"})
    assert await overridden.get(ORG, "carrier_key") == "ours"
    assert not await overridden.has(other, "carrier_key")


@pytest.mark.parametrize("name", ["", " x", "a/b", f"../{new_id()}/carrier_key", "..", "."])
async def test_a_name_never_climbs_out_of_its_tenant(tmp_path: Path, name: str) -> None:
    secrets = SecretsLocalImpl(tmp_path / "secrets.env")
    with pytest.raises(InvalidSecretName):
        await secrets.put(ORG, name, "v")
    with pytest.raises(InvalidSecretName):
        await secrets.get(ORG, name)


async def test_the_cloud_store_keeps_a_tenants_secret_under_its_prefix() -> None:
    from collections.abc import AsyncIterator
    from contextlib import asynccontextmanager
    from datetime import timedelta
    from typing import Any

    from acme.infra.secrets.aws import SecretsAwsImpl

    asked: list[str] = []

    class Client:
        async def get_secret_value(self, SecretId: str) -> dict[str, Any]:  # noqa: N803
            asked.append(SecretId)
            return {"SecretString": "v"}

    class Session:
        def client(self, *args: Any, **kwargs: Any) -> Any:
            @asynccontextmanager
            async def open_client() -> AsyncIterator[Client]:
                yield Client()

            return open_client()

    impl = SecretsAwsImpl(
        Session(),  # type: ignore[arg-type]
        region="r",
        name_prefix="acme/staging/app/",
        timeout=timedelta(seconds=1),
    )
    await impl.start()
    assert await impl.get(ORG, "carrier_key") == "v"
    await impl.close()
    assert asked == [f"acme/staging/app/org/{ORG}/carrier_key"]
