# Architecture review: Object Model

**Scope.** Path `/target/om`, 13 files, read from the working tree. `/target` is not a git repository. arch-check 0.37.0 ran with `--root /target --package acme --group om` because it could not find the package on its own, and it exited 1. It covered every lens it checks only in part.
**Lenses.** 16 applied, 5 passed, 11 findings, 0 unverified, 2 not applicable

## Findings

- **OM-16 medium** `om/src/acme/om/base.py:1` `acme.om` has no tenancy namespace and no audit namespace, even though managers already read `ctx.org_id` and `ctx.user_id`. Fix: add `tenancy` and `audit` namespaces, each with its own types, manager interface, and storage.
- **OM-10 medium** `om/src/acme/om/inventory/impl/manager.py:21` `update_warehouse` passes a `changes.model_dump(...)` to `current.model_copy(update=...)`, so the copy is never validated. Fix: rebuild it with `Warehouse.model_validate({**current.model_dump(), **changes, ...})`.
- **OM-12 medium** `om/src/acme/om/inventory/impl/manager.py:37` `create_bin` makes the `Bin` id with `uuid4()`, imported at line 1, instead of `new_id()`. Fix: use `new_id()` and drop the `uuid4` import.
- **OM-15 medium** `om/src/acme/om/inventory/rules.py:3` The rules module imports the clock helper `utcnow` from `acme.om.base`. Fix: remove the import so the module reads no clock.
- **OM-15 medium** `om/src/acme/om/inventory/rules.py:11` `is_stale` calls `utcnow()` inside the rule. Fix: take `now: datetime` as a parameter.
- **OM-14 medium** `om/src/acme/om/inventory/storage/__init__.py:23` `write_movement` is named for "movement" rather than the entity `StockMovement`, and `record_movement` in the manager has the same problem. Fix: rename it to `write_stock_movement`.
- **OM-03 medium** `om/src/acme/om/inventory/types/bin.py:12` `Bin` declares `created_at` again, although it already gets it from `Trackable` through `Created`. Fix: delete the local declaration.
- **OM-03 medium** `om/src/acme/om/inventory/types/bin.py:9` `Bin` declares no `MANAGER_OWNED_FIELDS`. Fix: declare the tuple, empty if nothing applies, and exclude it together with `PROVENANCE_FIELDS` when copying on update.
- **OM-13 medium** `om/src/acme/om/inventory/types/bin.py:6` `NO_BIN = UUID(int=0)` is a second all-zero sentinel next to `EMPTY_UUID`. Fix: delete `NO_BIN`.
- **OM-13 medium** `om/src/acme/om/inventory/types/bin.py:11` The optional reference `parent_bin_id` uses a sentinel to mean "no parent" instead of `None`. Fix: declare it `parent_bin_id: UUID | None = None`.
- **OM-03 medium** `om/src/acme/om/inventory/types/stock_movement.py:6` `StockMovement` declares no `MANAGER_OWNED_FIELDS`. Fix: declare the tuple, even if it is empty.
- **OM-05 medium** `om/src/acme/om/inventory/types/stock_movement.py:6` `StockMovement` composes `Trackable`, but no manager method ever sets its `updated_at` or `updated_by`. Fix: drop `Trackable` from its bases.
- **OM-03 medium** `om/src/acme/om/inventory/types/warehouse.py:4` `Warehouse` declares no `MANAGER_OWNED_FIELDS`. Fix: declare the tuple and exclude it together with `PROVENANCE_FIELDS` when copying in `update_warehouse`.
- **OM-17 medium** `om/src/acme/om/inventory/types/warehouse.py:6` `Warehouse.tags` is a `list[str]` with the mutable default `[]`. Fix: make it `tuple[str, ...] = ()`.
- **OM-06 low** `om/src/acme/om/inventory/types/stock_movement.py:6` `StockMovement` is an append-only ledger line ("Written once, never changed"), yet it composes `Trackable` and so carries `updated_at` and `updated_by`. Fix: compose `Identifiable` only.
- **OM-04 low** `om/src/acme/om/inventory/types/warehouse.py:4` `Warehouse(Trackable, Identifiable, Named, SoftDeletable)` puts lifecycle before identity and label. Fix: reorder to `Warehouse(Identifiable, Named, Trackable, SoftDeletable)`.

## Deviations

None.

## Passed

OM-01 (`om/pyproject.toml`), OM-02 (`om/src/acme/om/inventory/storage/__init__.py`), OM-07 (`om/src/acme/om/base.py`), OM-08 (`om/src/acme/om/inventory/types/warehouse.py`), OM-11 (`om/src/acme/om/base.py`)

## Unverified

None.

## Not applicable

OM-09 (no list or aggregate methods), OM-18 (no storage impl or statements in scope)
