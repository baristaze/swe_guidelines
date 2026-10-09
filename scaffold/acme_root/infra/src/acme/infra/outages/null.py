"""The outage signal of one process: it never marks. The process's own
breaker holds what its calls learned; what a shared signal adds is the news
for every other process, and one process has none to tell."""

from uuid import UUID

from acme.infra.outages import Outage, OutageSignalInterface


class OutageSignalNullImpl(OutageSignalInterface):
    async def mark(self, outage: Outage) -> None:
        return None

    async def current(self, org_id: UUID, provider: str, credential: str) -> Outage | None:
        return None

    async def clear(self, org_id: UUID, provider: str, credential: str) -> None:
        return None

    def describe(self) -> str:
        return "outages=none"

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None
