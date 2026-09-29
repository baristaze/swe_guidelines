"""The hosted secrets impl asks the store only what it needs: existence is
a describe, never a fetch of the value; a put is a create, and a new version
only when the store says the secret exists."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

import pytest
from botocore.exceptions import ClientError

from acme.infra.base import new_id
from acme.infra.exceptions import BackendFailed
from acme.infra.secrets.aws import SecretsAwsImpl

ORG = new_id()


def client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "from the driver"}}, "Op")


class RecordingClient:
    """A store with some secrets in it, recording every call by name."""

    def __init__(self, existing: set[str], describe_fails_with: str | None = None) -> None:
        self.existing = existing
        self.calls: list[tuple[str, str]] = []
        self._describe_fails_with = describe_fails_with

    async def describe_secret(self, **request: str) -> dict[str, Any]:
        name = request["SecretId"]
        self.calls.append(("describe_secret", name))
        if self._describe_fails_with:
            raise client_error(self._describe_fails_with)
        if name not in self.existing:
            raise client_error("ResourceNotFoundException")
        return {"Name": name}

    async def get_secret_value(self, **request: str) -> dict[str, Any]:
        name = request["SecretId"]
        self.calls.append(("get_secret_value", name))
        if name not in self.existing:
            raise client_error("ResourceNotFoundException")
        return {"SecretString": "the value"}

    async def create_secret(self, **request: str) -> dict[str, Any]:
        name = request["Name"]
        self.calls.append(("create_secret", name))
        if name in self.existing:
            raise client_error("ResourceExistsException")
        self.existing.add(name)
        return {"Name": name}

    async def put_secret_value(self, **request: str) -> dict[str, Any]:
        name = request["SecretId"]
        self.calls.append(("put_secret_value", name))
        assert name in self.existing
        return {"Name": name}

    async def delete_secret(self, **request: Any) -> dict[str, Any]:
        name = request["SecretId"]
        self.calls.append(("delete_secret", name))
        if name not in self.existing:
            raise client_error("ResourceNotFoundException")
        self.existing.remove(name)
        return {"Name": name}


class FakeSession:
    def __init__(self, client: RecordingClient) -> None:
        self.recording = client

    def client(self, *args: Any, **kwargs: Any) -> Any:
        @asynccontextmanager
        async def open_client() -> AsyncIterator[Any]:
            yield self.recording

        return open_client()


async def secrets(client: RecordingClient) -> SecretsAwsImpl:
    impl = SecretsAwsImpl(
        FakeSession(client),  # type: ignore[arg-type]
        region="us-east-1",
        name_prefix="acme/",
        timeout=timedelta(seconds=1),
    )
    await impl.start()
    return impl


async def test_has_describes_and_never_fetches_the_value() -> None:
    store = RecordingClient({f"acme/org/{ORG}/present"})
    impl = await secrets(store)
    assert await impl.has(ORG, "present") is True
    assert await impl.has(ORG, "absent") is False
    assert store.calls == [
        ("describe_secret", f"acme/org/{ORG}/present"),
        ("describe_secret", f"acme/org/{ORG}/absent"),
    ]


async def test_put_creates_and_falls_back_to_a_new_version() -> None:
    store = RecordingClient({f"acme/org/{ORG}/present"})
    impl = await secrets(store)
    await impl.put(ORG, "fresh", "v1")
    await impl.put(ORG, "present", "v2")
    assert store.calls == [
        ("create_secret", f"acme/org/{ORG}/fresh"),
        ("create_secret", f"acme/org/{ORG}/present"),
        ("put_secret_value", f"acme/org/{ORG}/present"),
    ]


async def test_any_other_answer_to_has_is_a_backend_failure() -> None:
    impl = await secrets(RecordingClient(set(), describe_fails_with="AccessDeniedException"))
    with pytest.raises(BackendFailed) as raised:
        await impl.has(ORG, "x")
    assert raised.value.message == "secretsmanager has failed with AccessDeniedException"


async def test_delete_is_idempotent_like_the_local_twin() -> None:
    store = RecordingClient({f"acme/org/{ORG}/present"})
    impl = await secrets(store)
    await impl.delete(ORG, "present")
    await impl.delete(ORG, "present")
    assert store.existing == set()
    assert store.calls == [
        ("delete_secret", f"acme/org/{ORG}/present"),
        ("delete_secret", f"acme/org/{ORG}/present"),
    ]
