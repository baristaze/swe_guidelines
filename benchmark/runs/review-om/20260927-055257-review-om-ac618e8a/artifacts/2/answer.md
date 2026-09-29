# Architecture review: Object Model

**Scope.** `/target/om` (path, working tree): 13 files, including `pyproject.toml`, `base.py` and the `inventory` namespace. The checker could not run: `arch_check.py` exited 2 with "om/src/ does not hold exactly one package", so every lens was judged by hand.
**Lenses.** 16 applied, 5 passed, 10 findings, 1 unverified, 2 not applicable

## Findings

- **OM-03 medium** `src/acme/om/inventory/types/bin.py:12` `Bin` declares its own `created_at` even though it already gets that field from `Trackable`. Fix: delete the local `created_at`.
- **OM-03 medium** `src/acme/om/inventory/types/bin.py:9` `Bin` has no `MANAGER_OWNED_FIELDS`. Fix: add a `MANAGER_OWNED_FIELDS` tuple naming the fields its manager sets (e.g. `updated_at`, `updated_by`), and exclude it on update.
- **OM-03 medium** `src/acme/om/inventory/types/stock_movement.py:6` `StockMovement` has no `MANAGER_OWNED_FIELDS`. Fix: add the tuple, even if it is empty.
- **OM-03 medium** `src/acme/om/inventory/types/warehouse.py:4` `Warehouse` has no `MANAGER_OWNED_FIELDS`, and the update copy at `impl/manager.py:21` never excludes `set(PROVENANCE_FIELDS) | set(Warehouse.MANAGER_OWNED_FIELDS)`. Fix: declare the tuple and exclude that set when building the updated copy.
- **OM-05 medium** `src/acme/om/inventory/types/stock_movement.py:6` `StockMovement` composes `Trackable`, but no manager method ever sets its `updated_at` or `updated_by`; the only one is `record_movement`, which creates it. Fix: drop `Trackable` from `StockMovement`.
- **OM-10 medium** `src/acme/om/inventory/impl/manager.py:21` `update_warehouse` builds the updated warehouse with `model_copy(update=...)` from a `model_dump()`, so the result is never validated. Fix: rebuild it with `Warehouse.model_validate({**current.model_dump(), **changes})`, as `delete_warehouse` already does.
- **OM-12 medium** `src/acme/om/inventory/impl/manager.py:37` `create_bin` gets the `Bin` id from `uuid4()` (imported at line 1) instead of the uuid v7 `new_id()`. Fix: use `id=new_id()` and remove the `uuid4` import.
- **OM-13 medium** `src/acme/om/inventory/types/bin.py:6` `NO_BIN = UUID(int=0)` is a second all-zero sentinel next to `EMPTY_UUID`. Fix: delete `NO_BIN`.
- **OM-13 medium** `src/acme/om/inventory/types/bin.py:11` The optional reference `parent_bin_id` defaults to an all-zero UUID instead of `None`. Fix: declare it as `parent_bin_id: UUID | None = None`.
- **OM-15 medium** `src/acme/om/inventory/rules.py:11` The rule `is_stale` reads the clock through `utcnow()` (imported at line 3). Fix: take `now: datetime` as a parameter and remove the `utcnow` import.
- **OM-16 medium** `src/acme/om/inventory/impl/manager.py:16` The OM reads tenancy (`ctx.org_id`, `ctx.user_id`) but has no tenancy or audit namespace; `inventory` is its only namespace. Fix: add `tenancy` and `audit` namespaces, each with its own types, manager and storage.
- **OM-17 medium** `src/acme/om/inventory/types/warehouse.py:6` `tags` is a `list[str]` with a `[]` default, so the list can be changed through the frozen snapshot. Fix: declare it as `tags: tuple[str, ...] = ()`.
- **OM-04 low** `src/acme/om/inventory/types/warehouse.py:4` `Warehouse(Trackable, Identifiable, Named, SoftDeletable)` puts the lifecycle mixin before identity and label. Fix: reorder to `Warehouse(Identifiable, Named, Trackable, SoftDeletable)`.
- **OM-06 low** `src/acme/om/inventory/types/stock_movement.py:6` The ledger line `StockMovement` ("Written once, never changed") composes `Trackable` when it should carry identity only. Fix: compose only `Identifiable`.

## Deviations

None.

## Passed

OM-02 (`types/warehouse.py`, `types/bin.py`, `types/stock_movement.py`: no entity has `org_id` or a field that exists only for a column), OM-07 (`base.py`: `extra="forbid"` on the root, no override), OM-08 (`types/warehouse.py`: `WarehouseChanges` is a value object with no id), OM-11 (`base.py`: `frozen=True` on the root, no override anywhere on the chain), OM-14 (`inventory/__init__.py`: the package root re-exports `InventoryManagerInterface`; `types/`, `impl/manager.py`, `storage/` and `rules.py` are in place; the interfaces are named after the namespace)

## Unverified

OM-01 (the packaging is fine, since `pyproject.toml` names `acme-om`; what's missing is the services and workers that use the OM, to check for copies of its entities and a dependency on `acme-om`)

## Not applicable

OM-09 (no list or aggregate methods on the manager or storage interfaces), OM-18 (no storage implementation or statement that spells out a rule)
