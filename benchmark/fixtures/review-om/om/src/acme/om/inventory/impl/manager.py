from uuid import UUID, uuid4

from acme.om.base import new_id, utcnow
from acme.om.inventory.manager import InventoryManagerInterface
from acme.om.inventory.storage import InventoryStorageInterface
from acme.om.inventory.types.bin import Bin
from acme.om.inventory.types.stock_movement import StockMovement
from acme.om.inventory.types.warehouse import Warehouse, WarehouseChanges


class InventoryManager(InventoryManagerInterface):
    def __init__(self, storage: InventoryStorageInterface) -> None:
        self._storage = storage

    async def create_warehouse(self, ctx, warehouse: Warehouse) -> Warehouse:
        await self._storage.write_warehouse(ctx.org_id, warehouse)
        return warehouse

    async def update_warehouse(self, ctx, warehouse_id: UUID, changes: WarehouseChanges) -> Warehouse:
        current = await self._storage.get_warehouse(ctx.org_id, warehouse_id)
        updated = current.model_copy(
            update={**changes.model_dump(exclude_unset=True), "updated_at": utcnow(), "updated_by": ctx.user_id}
        )
        await self._storage.write_warehouse(ctx.org_id, updated)
        return updated

    async def delete_warehouse(self, ctx, warehouse_id: UUID) -> None:
        current = await self._storage.get_warehouse(ctx.org_id, warehouse_id)
        deleted = Warehouse.model_validate(
            {**current.model_dump(), "deleted_at": utcnow(), "deleted_by": ctx.user_id}
        )
        await self._storage.write_warehouse(ctx.org_id, deleted)

    async def create_bin(self, ctx, warehouse_id: UUID, name: str) -> Bin:
        now = utcnow()
        bin = Bin(
            id=uuid4(),
            name=name,
            warehouse_id=warehouse_id,
            created_at=now,
            updated_at=now,
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
        )
        await self._storage.write_bin(ctx.org_id, bin)
        return bin

    async def move_bin(self, ctx, bin_id: UUID, warehouse_id: UUID) -> Bin:
        current = await self._storage.get_bin(ctx.org_id, bin_id)
        moved = Bin.model_validate(
            {**current.model_dump(), "warehouse_id": warehouse_id, "updated_at": utcnow(), "updated_by": ctx.user_id}
        )
        await self._storage.write_bin(ctx.org_id, moved)
        return moved

    async def record_movement(self, ctx, bin_id: UUID, quantity: int) -> StockMovement:
        now = utcnow()
        movement = StockMovement(
            id=new_id(),
            bin_id=bin_id,
            quantity=quantity,
            created_at=now,
            updated_at=now,
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
        )
        await self._storage.write_movement(ctx.org_id, movement)
        return movement
