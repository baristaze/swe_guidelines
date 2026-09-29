"""How much a tenant keeps in the store, counted from the rows."""

from acme.om.base import Platform
from acme.om.media.types.file import FilePurpose


class PurposeUsage(Platform):
    """The live files of one purpose: the stored ones, and the ones whose
    upload was started and not yet confirmed, which hold a place and may
    become stored."""

    purpose: FilePurpose
    count: int = 0
    size_bytes: int = 0
    pending_count: int = 0
    pending_size_bytes: int = 0


class StorageUsage(Platform):
    """One entry per purpose, every purpose present, in the order the enum
    declares them. A deleted file is not counted, whether or not the sweep has
    removed its object yet."""

    purposes: tuple[PurposeUsage, ...]

    @property
    def total_count(self) -> int:
        return sum(p.count for p in self.purposes)

    @property
    def total_size_bytes(self) -> int:
        return sum(p.size_bytes for p in self.purposes)

    @property
    def pending_size_bytes(self) -> int:
        return sum(p.pending_size_bytes for p in self.purposes)
