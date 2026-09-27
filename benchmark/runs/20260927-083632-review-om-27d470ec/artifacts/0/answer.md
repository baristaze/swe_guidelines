# Architecture review: Object Model

**Scope.** `/target/om`, all 13 files as they are now: the `acme-om` package with one namespace, `inventory`. The checker could not run. It exited 2 with "om/src/ does not hold exactly one package", so every lens was judged by reading the code.
**Lenses.** 16 applied, 6 passed, 10 findings, 0 unverified, 2 not applicable

## Findings

- **OM-10 medium** `src/acme/om/inventory/impl/manager.py:21` `update_warehouse` builds the update with `current.model_copy(update={**changes.model_dump(...), ...})`, which puts a dump into the copy without validating it. Fix: rebuild it with `Warehouse.model_validate({**current.model_dump(), **changes.model_dump(exclude_unset=True), ...})`, as `delete_warehouse` already does.
- **OM-12 medium** `src/acme/om/inventory/impl/manager.py:37` `create_bin` mints the `Bin` id with `uuid4()`, which is not time-ordered, instead of the base `new_id()` (uuid v7). Fix: use `id=new_id()` and drop the `uuid4` import.
- **OM-15 medium** `src/acme/om/inventory/rules.py:11` The rule `is_stale` reads the clock through `utcnow()`, so it is not pure. Fix: take `now: datetime` as an argument and drop the `utcnow` import from the rules module.
- **OM-16 medium** `src/acme/om/inventory/storage/__init__.py:14` Every operation is keyed by `org_id` and stamped with `ctx.user_id`, but the OM has no tenancy namespace that owns organizations and users, and no audit namespace. `inventory` is the only swimlane. Fix: add `tenancy` and `audit` namespaces with the same shape as `inventory`.
- **OM-13 medium** `src/acme/om/inventory/types/bin.py:6` `NO_BIN = UUID(int=0)` is a second all-zero sentinel next to the base `EMPTY_UUID`. Fix: delete `NO_BIN`.
- **OM-03 medium** `src/acme/om/inventory/types/bin.py:9` None of the entities declares `MANAGER_OWNED_FIELDS` (`Bin` here, and also `Warehouse` and `StockMovement`). The update copy in `update_warehouse` also does not exclude `set(PROVENANCE_FIELDS) | set(Warehouse.MANAGER_OWNED_FIELDS)`. Fix: declare the tuple on each entity (`()` when empty) and exclude that set when copying on update.
- **OM-13 medium** `src/acme/om/inventory/types/bin.py:11` The optional reference `parent_bin_id` expresses absence with an all-zero sentinel instead of `None`. Fix: declare it `parent_bin_id: UUID | None = None`.
- **OM-03 medium** `src/acme/om/inventory/types/bin.py:12` `Bin` declares its own `created_at` even though it already composes `Trackable`, which provides that field. Fix: remove the local `created_at`.
- **OM-05 medium** `src/acme/om/inventory/types/stock_movement.py:6` `StockMovement` composes `Trackable`, but no manager method ever sets its `updated_at` or `updated_by`. `record_movement` only creates it. Fix: drop `Trackable` from `StockMovement`.
- **OM-17 medium** `src/acme/om/inventory/types/warehouse.py:6` `tags: list[str] = []` is a mutable `list` field on a frozen entity. Fix: declare it `tags: tuple[str, ...] = ()`.
- **OM-06 low** `src/acme/om/inventory/types/stock_movement.py:6` A ledger line documented as "Written once, never changed" is composed with `Trackable`, so it carries an `updated_at` that no code path writes. Fix: make it `Identifiable` only, keeping the fields it needs for itself.
- **OM-04 low** `src/acme/om/inventory/types/warehouse.py:4` `Warehouse(Trackable, Identifiable, Named, SoftDeletable)` puts lifecycle before identity and label. Fix: reorder to `Warehouse(Identifiable, Named, Trackable, SoftDeletable)`.

## Deviations

None.

## Passed

OM-01 (`pyproject.toml`), OM-02 (`src/acme/om/inventory/types/warehouse.py`), OM-07 (`src/acme/om/base.py`), OM-08 (`src/acme/om/inventory/types/warehouse.py`), OM-11 (`src/acme/om/base.py`), OM-14 (`src/acme/om/inventory/__init__.py`)

## Unverified

None.

## Not applicable

OM-09 (no list or aggregate methods), OM-18 (no storage impl or statements in scope)
