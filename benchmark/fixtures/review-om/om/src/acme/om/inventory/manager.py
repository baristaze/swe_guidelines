from abc import ABC, abstractmethod
from uuid import UUID

from acme.om.inventory.types.bin import Bin
from acme.om.inventory.types.stock_movement import StockMovement
from acme.om.inventory.types.warehouse import Warehouse, WarehouseChanges


class InventoryManagerInterface(ABC):
    @abstractmethod
    async def create_warehouse(self, ctx, warehouse: Warehouse) -> Warehouse: ...

    @abstractmethod
    async def update_warehouse(self, ctx, warehouse_id: UUID, changes: WarehouseChanges) -> Warehouse: ...

    @abstractmethod
    async def delete_warehouse(self, ctx, warehouse_id: UUID) -> None: ...

    @abstractmethod
    async def create_bin(self, ctx, warehouse_id: UUID, name: str) -> Bin: ...

    @abstractmethod
    async def move_bin(self, ctx, bin_id: UUID, warehouse_id: UUID) -> Bin: ...

    @abstractmethod
    async def record_movement(self, ctx, bin_id: UUID, quantity: int) -> StockMovement: ...
