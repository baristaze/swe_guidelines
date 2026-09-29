"""What every infra module shares and nothing above infra provides: the
frozen model base, the system scope, and the two helpers a twin needs. Infra
imports nothing from the object model; the object model imports infra."""

from datetime import UTC, datetime
from uuid import UUID, uuid7

from pydantic import BaseModel, ConfigDict

SYSTEM_SCOPE = UUID(int=0)
"""The reserved system scope, by value: the object model's `EMPTY_UUID` is
the same UUID, and infra checks against it without importing the model."""


def new_id() -> UUID:
    """A time-ordered UUID v7 as a standard-library UUID."""
    return uuid7()


def utcnow() -> datetime:
    return datetime.now(UTC)


class InfraModel(BaseModel):
    """Root of the values infra hands out. Frozen, strict on unknown fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")
