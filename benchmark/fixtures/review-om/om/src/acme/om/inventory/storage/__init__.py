from abc import ABC, abstractmethod
from uuid import UUID

from acme.om.inventory.types.bin import Bin
from acme.om.inventory.types.stock_movement import StockMovement
from acme.om.inventory.types.warehouse import Warehouse


class InventoryStorageInterface(ABC):
    @abstractmethod
    async def get_warehouse(self, org_id: UUID, warehouse_id: UUID) -> Warehouse | None: ...

    @abstractmethod
    async def write_warehouse(self, org_id: UUID, warehouse: Warehouse) -> None: ...

    @abstractmethod
    async def get_bin(self, org_id: UUID, bin_id: UUID) -> Bin | None: ...

    @abstractmethod
    async def write_bin(self, org_id: UUID, bin: Bin) -> None: ...

    @abstractmethod
    async def write_movement(self, org_id: UUID, movement: StockMovement) -> None: ...
