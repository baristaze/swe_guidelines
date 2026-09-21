from datetime import UTC, datetime
from uuid import UUID, uuid7

from pydantic import BaseModel, ConfigDict


class Platform(BaseModel):
    """Root of the object model. Holds no fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class Identifiable(Platform):
    id: UUID


class Named(Platform):
    name: str


class Created(Platform):
    created_at: datetime


class Trackable(Created):
    updated_at: datetime
    created_by: UUID
    updated_by: UUID


class SoftDeletable(Platform):
    deleted_at: datetime | None = None
    deleted_by: UUID | None = None


PROVENANCE_FIELDS = frozenset({"created_at", "created_by", "deleted_at", "deleted_by"})

EMPTY_UUID = UUID(int=0)


def new_id() -> UUID:
    return uuid7()


def utcnow() -> datetime:
    return datetime.now(UTC)
