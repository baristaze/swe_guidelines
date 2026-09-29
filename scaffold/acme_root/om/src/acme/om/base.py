"""Root of the object model: the base class, the mixins, and the two helpers
every entity constructor needs."""

import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Annotated, Any
from uuid import UUID, uuid7

from pydantic import AfterValidator, BaseModel, ConfigDict, PlainSerializer


def new_id() -> UUID:
    """A time-ordered UUID v7 as a standard-library UUID."""
    return uuid7()


def derived_id(key: UUID, at: datetime, part: str = "") -> UUID:
    """The one id not minted fresh: a v7 whose time is `at` and whose random
    bits are taken from `key`, and from `part` when one key makes many (an
    import's rows). An entity an outside delivery creates takes it, with the
    delivery's key and the time the platform received it, so the same
    delivery handled twice presents the same id and the create meets the row
    already there; so does a row of an import stepped twice. It sorts by time
    like every other id."""
    millis = int(at.timestamp() * 1000) & ((1 << 48) - 1)
    digest = hashlib.sha256(key.bytes + part.encode()).digest()
    tail = int.from_bytes(digest[:10], "big")
    rand_a = tail >> 68  # 12 bits
    rand_b = tail & ((1 << 62) - 1)  # 62 bits
    value = (millis << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    return UUID(int=value)


def utcnow() -> datetime:
    return datetime.now(UTC)


class Platform(BaseModel):
    """Root of the object model. Holds no fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")


def _frozen(value: Any) -> Any:
    """A mapping becomes a read-only view of frozen values, a list a tuple of
    them, and anything else travels as it is."""
    if isinstance(value, Mapping):
        return MappingProxyType({key: _frozen(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_frozen(item) for item in value)
    return value


def _plain(value: Any) -> Any:
    """The way back: plain dicts and lists, the containers JSON has."""
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(item) for item in value]
    return value


def freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType({key: _frozen(item) for key, item in value.items()})


def thaw_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: _plain(item) for key, item in value.items()}


FrozenMapping = Annotated[
    Mapping[str, Any],
    AfterValidator(freeze_mapping),
    PlainSerializer(thaw_mapping, return_type=dict),
]
"""A mapping field that stays frozen past the model: pydantic validates a
`Mapping` into a dict, so a read-only view is put around it. The freeze
reaches what the mapping holds, because a proxy freezes only the mapping it
wraps and a payload of dumped JSON is nested: a nested mapping is wrapped the
same way and a nested list becomes a tuple. The serializer rebuilds plain
dicts and lists on the way out, so a stored payload is JSON again. The empty
case is `Field(default_factory=dict, validate_default=True)`, or the default
is the one dict that escapes the freeze."""


class Identifiable(Platform):
    id: UUID  # uuid_v7, from new_id()


class Named(Platform):
    name: str


class Created(Platform):
    """When a row came to be. For a record that is written once and never
    edited (an idempotency record, a socket ticket); an entity that changes
    composes `Trackable` instead."""

    created_at: datetime


class Trackable(Created):
    updated_at: datetime
    created_by: UUID  # id of the user who created it
    updated_by: UUID  # id of the user who last changed it


class SoftDeletable(Platform):
    deleted_at: datetime | None = None
    deleted_by: UUID | None = None


PROVENANCE_FIELDS = frozenset({"created_at", "created_by", "deleted_at", "deleted_by"})
"""Who made a row and who deleted it. The copy on update starts from the
stored row and takes the caller's fields with these excluded, so no caller
rewrites who made a row or brings a deleted one back by sending an entity.

Beside it, each entity that a caller updates by sending it declares
`MANAGER_OWNED_FIELDS`, a `ClassVar[tuple[str, ...]]`: the fields its manager
sets through the operations that own them and a caller never writes. The copy
on update excludes both sets."""


EMPTY_UUID = UUID(int=0)
"""The platform, not a tenant or a person: the reserved system scope, and the
value of a required reference that no tenant and no person owns. It equals
infra's `SYSTEM_SCOPE` by value, so infra never imports it. A reference that
is genuinely optional is `None`, never `EMPTY_UUID`."""
