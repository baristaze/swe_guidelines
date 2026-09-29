import asyncio
import stat
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from uuid import UUID

from acme.infra.secrets import (
    SecretNotFound,
    SecretsFileNotPrivate,
    SecretsInterface,
    scoped_name,
)


class SecretsLocalImpl(SecretsInterface):
    """The overrides the settings object collected at boot
    (ACME_SECRET_<ORG>_<NAME> in the environment, the org id in hex, keyed
    by what follows the prefix), then an owner-only file of
    `org/<org_id>/<name>=value` lines. Writes go to the file. Nothing here
    reads the environment."""

    def __init__(self, file: Path | None, overrides: Mapping[str, str] | None = None) -> None:
        self._file = file
        self._overrides = dict(overrides or {})

    async def get(self, org_id: UUID, name: str, *, deadline: datetime | None = None) -> str:
        key = scoped_name(org_id, name)
        value = self._overrides.get(_override_key(org_id, name))
        if value is not None:
            return value
        entries = await asyncio.to_thread(self._read_file)
        if key in entries:
            return entries[key]
        raise SecretNotFound(name, "local")

    async def has(self, org_id: UUID, name: str) -> bool:
        key = scoped_name(org_id, name)
        if _override_key(org_id, name) in self._overrides:
            return True
        return key in await asyncio.to_thread(self._read_file)

    async def put(
        self, org_id: UUID, name: str, value: str, *, deadline: datetime | None = None
    ) -> None:
        key = scoped_name(org_id, name)

        def write() -> None:
            entries = self._read_file()
            entries[key] = value
            self._write_file(entries)

        await asyncio.to_thread(write)

    async def delete(self, org_id: UUID, name: str, *, deadline: datetime | None = None) -> None:
        key = scoped_name(org_id, name)

        def write() -> None:
            entries = self._read_file()
            entries.pop(key, None)
            self._write_file(entries)

        await asyncio.to_thread(write)

    def describe(self) -> str:
        return f"secrets=local({self._file or 'env only'})"

    def _read_file(self) -> dict[str, str]:
        if self._file is None or not self._file.exists():
            return {}
        mode = stat.S_IMODE(self._file.stat().st_mode)
        if mode & 0o077:
            raise SecretsFileNotPrivate(f"{self._file} must be owner-only (mode 600), is {mode:o}")
        entries: dict[str, str] = {}
        for line in self._file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            entries[key.strip()] = value.strip()
        return entries

    def _write_file(self, entries: dict[str, str]) -> None:
        if self._file is None:
            raise SecretsFileNotPrivate("no secrets file configured; set ACME_SECRETS_FILE")
        self._file.parent.mkdir(parents=True, exist_ok=True)
        self._file.touch(mode=0o600, exist_ok=True)
        self._file.chmod(0o600)
        self._file.write_text("".join(f"{k}={v}\n" for k, v in sorted(entries.items())))

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None


def _override_key(org_id: UUID, name: str) -> str:
    """ACME_SECRET_<ORG>_<NAME>, less the prefix: an override is a tenant's
    too, never a name every tenant resolves."""
    return f"{org_id.hex}_{name}".upper()
