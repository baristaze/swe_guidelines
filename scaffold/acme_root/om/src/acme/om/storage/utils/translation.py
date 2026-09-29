"""Translation between an OM entity and a table row, for the case where the
field names match one-to-one. JSON columns take the JSON-mode dump; scalars
travel natively; enums travel as their value."""

from enum import Enum
from typing import Any

from pydantic import BaseModel
from sqlalchemy import inspect
from sqlalchemy.sql import sqltypes


def to_row[R](entity: BaseModel, row_type: type[R], **extra: Any) -> R:
    """Build a row from an entity; `extra` carries storage-only columns such as org_id."""
    row = row_type()
    apply_row(row, entity)
    for name, value in extra.items():
        setattr(row, name, value)
    return row


def to_model[M: BaseModel](row: Any, model_type: type[M]) -> M:
    return model_type.model_validate(row, from_attributes=True)


def apply_row(row: Any, entity: BaseModel) -> None:
    """Copy entity values onto an existing row in place, never touching org_id."""
    for name, value in to_values(entity, type(row)).items():
        setattr(row, name, value)


def to_values(entity: BaseModel, row_type: type[Any]) -> dict[str, Any]:
    """The column values of an entity, for a statement that writes columns by
    name; org_id and columns the entity has no field for are left out."""
    columns = inspect(row_type).columns
    dumped: dict[str, Any] | None = None
    values: dict[str, Any] = {}
    for name in type(entity).model_fields:
        if name == "org_id" or name not in columns:
            continue
        value = getattr(entity, name)
        if isinstance(columns[name].type, sqltypes.JSON):
            if dumped is None:
                dumped = entity.model_dump(mode="json")
            value = dumped[name]
        elif isinstance(value, Enum):
            value = value.value
        values[name] = value
    return values


def undeletes(stored: Any, entity: Any) -> bool:
    """True when writing `entity` over `stored` would clear a soft delete.
    Takes a table row or an entity on either side: both spell the field
    `deleted_at`, and a type without one is never soft-deleted."""
    return (
        getattr(stored, "deleted_at", None) is not None
        and getattr(entity, "deleted_at", None) is None
    )
