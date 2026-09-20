---
name: arch-scaffold-namespace
description: "Create a new object-model namespace (swimlane) with the shape the Software Design and Architecture Guidelines prescribe: the manager interface at the root, types, impl, storage with Postgres and memory impls, tables, and wiring into the storage and business roots. Stack: Python (FastAPI, Pydantic, SQLAlchemy)."
allowed-tools: Read, Grep, Glob, Write, Edit, Bash(make check), Bash(make infra-up), Bash(make migrate), Bash(make migrate-check), Bash(make openapi), Bash(uv run:*), Bash(git status:*)
---

# arch-scaffold-namespace

Conventions: `${CLAUDE_SKILL_DIR}/../_shared/scaffold-conventions.md`.
Sections of `${CLAUDE_SKILL_DIR}/../../architecture.md`: Namespaces as
Swimlanes, Interfaces (Injectability), The Business Layer
(Cross-Manager Dependencies), The Storage Layer (Namespace Shape,
Storage Root, Cross-Storage Dependencies).

## Input

`<namespace> [FirstEntity] [field:type ...] [--role core|activity|queue|admin]`

Example: `inventory Warehouse address:str timezone:str`. `<namespace>`
is required; ask for it when missing. `<Ns>` is the namespace in
CamelCase. When a first entity is named, the entity arguments and the
role are forwarded to the entity skill in step 3; otherwise the
namespace is created empty and ready.

## Created

Under `om/src/<root>/om/<ns>/`:

| File                       | Holds                                                                   |
|----------------------------|-------------------------------------------------------------------------|
| `__init__.py`              | `from .manager import <Ns>ManagerInterface`                             |
| `manager.py`               | `<Ns>ManagerInterface`, a docstring naming the swimlane, no methods yet |
| `types/__init__.py`        | empty; the entity skill adds one module per entity                      |
| `impl/__init__.py`         | empty                                                                   |
| `impl/manager.py`          | `<Ns>ManagerImpl(<Ns>ManagerInterface)` taking `<Ns>StorageInterface` and `OutboxRelayInterface`, writing the core row and its `OutboxRow` in one storage call on every write and relaying the row at once; the relay appends the `Event` and publishes `ENTITY_CHANGED`, so the realtime channel has a producer |
| `storage/__init__.py`      | `<Ns>StorageInterface`, a docstring, no methods yet                     |
| `storage/impl/__init__.py` | empty                                                                   |
| `storage/impl/postgres.py` | `<Ns>StoragePostgresImpl(PgStorageBase, <Ns>StorageInterface)`          |
| `storage/impl/memory.py`   | `<Ns>StorageMemoryImpl(MemoryStorageBase, <Ns>StorageInterface)`        |
| `storage/tables/__init__.py` | empty; the entity skill adds one module per table                     |

## Changed

| File                                      | Change                                                            |
|-------------------------------------------|-------------------------------------------------------------------|
| `om/src/<root>/om/storage/root.py`         | `get_<ns>_storage() -> <Ns>StorageInterface` on `StorageInterface` |
| `om/src/<root>/om/storage/impl/postgres.py` | constructs `<Ns>StoragePostgresImpl` and returns it from the getter |
| `om/src/<root>/om/storage/impl/memory.py`  | constructs `<Ns>StorageMemoryImpl` and returns it from the getter  |
| `om/src/<root>/om/root.py`                 | constructs `<Ns>ManagerImpl` and adds field `<ns>` to `Managers`    |
| `om/tests/unit/test_roots.py` (or the existing root test) | asserts the new getter and the new manager field         |

## Procedure

1. Create the files in the table, then wire the roots.
2. A cross-manager dependency the new manager needs is a constructor
   parameter typed by interface and a wiring line in `root.py`; the
   interface stays untouched.
3. When a first entity was named, read
   `${CLAUDE_SKILL_DIR}/../arch-scaffold-entity/SKILL.md` and follow
   its Created, Changed, and Procedure with these arguments:
   `<namespace> <FirstEntity> <field:type ...> --role <role>`.

## Output

As `${CLAUDE_SKILL_DIR}/../_shared/scaffold-conventions.md` states.
