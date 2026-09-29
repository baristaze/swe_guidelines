"""Every timestamp column stores its offset. A naive `timestamp` reads back
without one, and a comparison against an aware `utcnow()` then raises or,
worse, compares wall clocks; the base's type map makes `Mapped[datetime]`
aware, and this holds every table to it, including a column typed by hand."""

from sqlalchemy import DateTime
from unit.test_roles import import_every_table_module

from acme.om.storage.tables.base import Base


def test_every_datetime_column_is_timezone_aware() -> None:
    import_every_table_module()
    naive = [
        f"{table.fullname}.{column.name}"
        for table in Base.metadata.tables.values()
        for column in table.columns
        if isinstance(column.type, DateTime) and not column.type.timezone
    ]
    assert Base.metadata.tables, "no tables were imported"
    assert naive == []
