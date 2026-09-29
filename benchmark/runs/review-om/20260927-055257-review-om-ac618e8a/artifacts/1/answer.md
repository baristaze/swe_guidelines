# Architecture review: Object Model

**Scope.** Every file under `/target/om` (13 files: the `acme-om` distribution, its `base.py` and the `inventory` namespace). `arch-check` could not run and exited 2 twice. The first run said `om/src/` does not hold exactly one package, because `acme` is a namespace package. With `--package acme.om`, it said its source globs match no Python file. Every lens was judged by hand.
**Lenses.** 16 applied, 5 passed, 9 findings, 2 unverified, 2 not applicable

## Findings

- **OM-03 medium** `om/src/acme/om/inventory/types/bin.py:12` `Bin` declares its own `created_at` even though it already gets that field from `Trackable`. Fix: delete the local `created_at` so the field comes only from the mixin.
- **OM-03 medium** `om/src/acme/om/inventory/types/bin.py:9` `Bin` has no `MANAGER_OWNED_FIELDS` tuple, though `move_bin` sets `warehouse_id` itself. Fix: declare `MANAGER_OWNED_FIELDS = ("warehouse_id",)` or whatever the manager owns, and exclude it and `PROVENANCE_FIELDS` from the update copy.
- **OM-03 medium** `om/src/acme/om/inventory/types/stock_movement.py:6` `StockMovement` has no `MANAGER_OWNED_FIELDS` tuple. Fix: declare `MANAGER_OWNED_FIELDS = ()`.
- **OM-03 medium** `om/src/acme/om/inventory/types/warehouse.py:4` `Warehouse` has no `MANAGER_OWNED_FIELDS` tuple. Also, the update copy at `impl/manager.py:21` does not exclude `set(PROVENANCE_FIELDS) | set(Warehouse.MANAGER_OWNED_FIELDS)`. Fix: declare the tuple and filter the changes through that exclusion before copying.
- **OM-05 medium** `om/src/acme/om/inventory/types/stock_movement.py:6` `StockMovement` composes `Trackable`, but no manager method ever updates it (`record_movement` only creates it). Fix: drop `Trackable`, since no operation exercises that promise.
- **OM-10 medium** `om/src/acme/om/inventory/impl/manager.py:21` `update_warehouse` passes a `model_dump()` of the request to `model_copy(update=...)`, which skips validation. An explicit `name=None` would land unvalidated in a `str` field. Fix: rebuild with `Warehouse.model_validate({**current.model_dump(), **changes..., ...})`, as `delete_warehouse` already does.
- **OM-12 medium** `om/src/acme/om/inventory/impl/manager.py:37` `create_bin` mints the entity id with `uuid4()` (imported at line 1) instead of `new_id()`. Fix: use `new_id()` from `acme.om.base` and remove the `uuid4` import.
- **OM-13 medium** `om/src/acme/om/inventory/types/bin.py:6` `NO_BIN = UUID(int=0)` is a second all-zero sentinel, and the optional reference `parent_bin_id` defaults to it at line 11. Fix: type the field `parent_bin_id: UUID | None = None` and delete `NO_BIN`.
- **OM-15 medium** `om/src/acme/om/inventory/rules.py:11` `is_stale` reads the clock through `utcnow()` inside a rules function. Fix: take `now: datetime` as a parameter and let the caller pass `utcnow()`.
- **OM-17 medium** `om/src/acme/om/inventory/types/warehouse.py:6` `tags: list[str] = []` is a mutable list field on a frozen entity. Fix: declare `tags: tuple[str, ...] = ()`.
- **OM-06 low** `om/src/acme/om/inventory/types/stock_movement.py:6` `StockMovement` is a ledger line its docstring calls "written once, never changed", yet it composes `Trackable` and so carries `updated_at` and `updated_by`. Fix: compose `Identifiable` only.
- **OM-04 low** `om/src/acme/om/inventory/types/warehouse.py:4` The bases are listed as `(Trackable, Identifiable, Named, SoftDeletable)`, which puts lifecycle before identity. Fix: reorder to `(Identifiable, Named, Trackable, SoftDeletable)`.

## Deviations

None.

## Passed

OM-02 (no entity carries `org_id`; storage takes the tenant as a parameter in `storage/__init__.py`), OM-07 (`base.py`: `extra="forbid"` on `Platform`, no class overrides it), OM-08 (`WarehouseChanges` has no identity and there are no read models), OM-11 (`base.py`: `frozen=True` on the root, no class on the chain overrides it), OM-14 (`inventory/`: `manager.py` is re-exported from `__init__.py`, and `types/`, `impl/manager.py`, `storage/` and `rules.py` are present, named `InventoryManagerInterface`, `InventoryStorageInterface` and `write_warehouse`)

## Unverified

OM-01 (the OM is its own `acme-om` distribution, but whether services, workers and apps import from it or redeclare entities is outside the scope), OM-16 (the OM has no tenancy or audit namespace, yet `ctx.org_id` and `ctx.user_id` are used; where Organization, User, Membership and audit types are declared would decide it)

## Not applicable

OM-09 (no list or aggregate methods take filters), OM-18 (no storage impl or statement in scope spells a rule)
