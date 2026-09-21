from acme.om.base import Identifiable, Named, Platform, SoftDeletable, Trackable


class Warehouse(Trackable, Identifiable, Named, SoftDeletable):
    capacity: int
    tags: list[str] = []


class WarehouseChanges(Platform):
    """The fields an update may set. An absent field means unchanged."""

    name: str | None = None
    capacity: int | None = None
