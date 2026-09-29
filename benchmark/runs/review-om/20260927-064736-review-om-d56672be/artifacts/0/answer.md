# Architecture review: Object Model

**Scope.** Everything under `/target/om` (13 files: `pyproject.toml`, `acme.om.base`, and the `inventory` namespace), read from the working tree. The directory is not a git repo. `arch-check` exited with code 2 ("om/src/ does not hold exactly one package"), so every lens was judged by hand.
**Lenses.** 16 applied, 6 passed, 9 findings, 1 unverified, 2 not applicable

## Findings

- **OM-10 medium** `src/acme/om/inventory/impl/manager.py:21` `update_warehouse` passes `changes.model_dump(...)` to `current.model_copy(update=...)`, which skips validation for caller input. Fix: rebuild with `Warehouse.model_validate({**current.model_dump(), **changes.model_dump(exclude_unset=True), ...})`.
- **OM-12 medium** `src/acme/om/inventory/impl/manager.py:37` `create_bin` mints the entity id with `uuid4()` instead of the base `new_id()` (uuid v7). Fix: use `id=new_id()` and drop the `uuid4` import.
- **OM-15 medium** `src/acme/om/inventory/rules.py:11` The rule `is_stale` reads the clock through `utcnow()`, so it is not a pure function. Fix: take `now: datetime` as an argument and let the manager pass `utcnow()`.
- **OM-13 medium** `src/acme/om/inventory/types/bin.py:6` `NO_BIN = UUID(int=0)` is a second all-zero sentinel next to the base `EMPTY_UUID`. Fix: delete `NO_BIN`.
- **OM-03 medium** `src/acme/om/inventory/types/bin.py:9` `Bin` does not declare `MANAGER_OWNED_FIELDS`, although `move_bin` sets `warehouse_id`. Fix: declare the tuple and list in it the fields the manager sets.
- **OM-13 medium** `src/acme/om/inventory/types/bin.py:11` The optional reference `parent_bin_id` defaults to an all-zero UUID instead of `None`. Fix: type it `UUID | None = None`.
- **OM-03 medium** `src/acme/om/inventory/types/bin.py:12` `Bin` declares its own `created_at` even though it already inherits it through `Trackable`. Fix: remove the local declaration.
- **OM-03 medium** `src/acme/om/inventory/types/stock_movement.py:6` `StockMovement` does not declare `MANAGER_OWNED_FIELDS`. Fix: declare it, as `()` if the manager sets nothing.
- **OM-05 medium** `src/acme/om/inventory/types/stock_movement.py:6` `StockMovement` composes `Trackable`, but no manager method ever updates it. `updated_at`/`updated_by` are only stamped at creation in `record_movement`. Fix: drop `Trackable` (see OM-06).
- **OM-03 medium** `src/acme/om/inventory/types/warehouse.py:4` `Warehouse` does not declare `MANAGER_OWNED_FIELDS`. Fix: declare the tuple, even if it is empty.
- **OM-17 medium** `src/acme/om/inventory/types/warehouse.py:6` The field `tags: list[str] = []` is a mutable `list` on a frozen entity. Fix: make it `tuple[str, ...] = ()`.
- **OM-06 low** `src/acme/om/inventory/types/stock_movement.py:6` The ledger line `StockMovement`, whose docstring says it is written once and never changed, composes `Trackable`. Fix: compose `Identifiable` only.
- **OM-04 low** `src/acme/om/inventory/types/warehouse.py:4` The bases are ordered `Trackable, Identifiable, Named, SoftDeletable`, putting lifecycle before identity and label. Fix: reorder to `Identifiable, Named, Trackable, SoftDeletable`.

## Deviations

None.

## Passed

OM-01 (`pyproject.toml`), OM-02 (`src/acme/om/inventory/types/`), OM-07 (`src/acme/om/base.py`), OM-08 (`src/acme/om/inventory/types/warehouse.py`), OM-11 (`src/acme/om/base.py`), OM-14 (`src/acme/om/inventory/`)

- **OM-01:** `acme-om` is its own distribution, and nothing in scope redeclares an entity.
- **OM-02:** No entity carries `org_id`. The storage layer takes it as a separate argument.
- **OM-07:** The root sets `extra="forbid"`, and no class overrides it.
- **OM-08:** Only the entities are `Identifiable`. `WarehouseChanges` has no id.
- **OM-11:** The root sets `frozen=True`, and no class on the base chain overrides it.
- **OM-14:** The namespace has `manager.py` re-exported from the package root, plus `types/`, `impl/`, `storage/` and `rules.py`. The interfaces are named `InventoryManagerInterface`/`InventoryStorageInterface`.

## Unverified

OM-16: the manager uses `ctx.org_id` and `ctx.user_id`, but this OM has no tenancy or audit namespace. What would decide it is where the organization, user and audit types are defined.

## Not applicable

OM-09 (the manager and storage interfaces have no list or aggregate methods), OM-18 (there is no storage impl or statement in scope)
