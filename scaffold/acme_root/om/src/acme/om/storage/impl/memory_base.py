"""The in-memory base: a full second implementation of every tenancy rule
the relational base has, over dicts keyed by id. The outbox rows land in the
outbox memory storage the root handed this impl, the twin of "in the same
commit"."""

import asyncio
from collections.abc import Iterator
from typing import Protocol, TypeVar
from uuid import UUID

from acme.om.exceptions import RowDeleted, TenantMismatch
from acme.om.outbox.storage import OutboxLandingInterface
from acme.om.outbox.types.row import OutboxRow
from acme.om.storage.utils.translation import undeletes


class HasId(Protocol):
    @property
    def id(self) -> UUID: ...


E = TypeVar("E", bound=HasId)

MemoryTable = dict[UUID, tuple[UUID, E]]
"""id -> (org_id, entity). Both impls sort by the UUID value, never by its string."""


class MemoryStorageBase:
    def __init__(self, outbox: OutboxLandingInterface | None = None) -> None:
        self._lock = asyncio.Lock()
        self._outbox = outbox

    def _fence(self, table: MemoryTable[E], org_id: UUID, entity: E) -> None:
        """The tenant fence of a write, on its own so a caller that must refuse
        before it spends anything can ask first. `_put` asks it too, so the
        fence has one spelling."""
        existing = table.get(entity.id)
        if existing is not None and existing[0] != org_id:
            raise TenantMismatch(f"{entity.id} is not in {org_id}")
        if existing is not None and undeletes(existing[1], entity):
            raise RowDeleted(f"{entity.id} was deleted")

    def _put(
        self,
        table: MemoryTable[E],
        org_id: UUID,
        entity: E,
        outbox_rows: tuple[OutboxRow, ...] = (),
    ) -> None:
        self._fence(table, org_id, entity)
        self._land(org_id, outbox_rows)
        table[entity.id] = (org_id, entity)

    def _insert(
        self,
        table: MemoryTable[E],
        org_id: UUID,
        entity: E,
        outbox_rows: tuple[OutboxRow, ...] = (),
    ) -> bool:
        """The create primitive: False when the id is already written, and nothing
        changes then, the outbox rows included."""
        if entity.id in table:
            return False
        self._land(org_id, outbox_rows)
        table[entity.id] = (org_id, entity)
        return True

    def _land(self, org_id: UUID, outbox_rows: tuple[OutboxRow, ...]) -> None:
        """The twin of landing the rows in the same commit: an entity change is
        one row, and a write that also starts work carries a second one of kind
        `work.<kind>`, which the relay turns into a work item."""
        if not outbox_rows:
            return
        if self._outbox is None:
            raise RuntimeError("this memory storage was built without an outbox to land in")
        for row in outbox_rows:
            self._outbox.land(org_id, row)

    @staticmethod
    def _get(table: MemoryTable[E], org_id: UUID, entity_id: UUID) -> E | None:
        found = table.get(entity_id)
        if found is None or found[0] != org_id:
            return None
        return found[1]

    @staticmethod
    def _rows(table: MemoryTable[E], org_id: UUID) -> list[E]:
        return sorted(
            (entity for row_org, entity in table.values() if row_org == org_id),
            key=lambda entity: entity.id,
        )

    @staticmethod
    def _rows_across_tenants(table: MemoryTable[E]) -> list[tuple[UUID, E]]:
        return sorted(table.values(), key=lambda pair: pair[1].id)

    @staticmethod
    def _every(table: MemoryTable[E]) -> Iterator[E]:
        """Every entity of a table, any tenant, unordered: for a key that is
        unique across tenants (a token hash, a slug)."""
        return (entity for _, entity in table.values())
