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
Storage Root, Cross-Storage Dependencies, The Second Fence),
Documentation as Code (A README at Every Level).

## Input

`<namespace> [FirstEntity] [field:type ...] [--role core|activity|queue|admin] [--scope system|org|identity|both]`

Example: `inventory Warehouse address:str timezone:str`. `<namespace>`
is required; ask for it when missing. `<Ns>` is the namespace in the
singular, in CamelCase (`orders` is `Order`, `inventory` is
`Inventory`), as Namespaces as Swimlanes names interfaces and
getters. `<ns_singular>` is the same singular in snake case (`orders`
gives `order`), which names the storage root's getter. Ask for the
singular when it is not a plain one. When a first entity is named,
the entity arguments, the role, and the scope are forwarded to the entity skill
in step 3; otherwise the namespace is created empty and ready.

## Created

Under `om/src/<root>/om/<ns>/`:

| File                       | Holds                                                                   |
|----------------------------|-------------------------------------------------------------------------|
| `__init__.py`              | `from .manager import <Ns>ManagerInterface`                             |
| `README.md`                | the namespace one level below `om/README.md`, in the product's language: what its nouns are, what can happen to them, and which rules hold; no developer or operator instruction; the entity skill adds each noun it creates |
| `manager.py`               | `<Ns>ManagerInterface`, a docstring naming the swimlane, no methods yet |
| `types/__init__.py`        | empty; the entity skill adds one module per entity                      |
| `impl/__init__.py`         | empty                                                                   |
| `impl/manager.py`          | `<Ns>ManagerImpl(<Ns>ManagerInterface)` taking `<Ns>StorageInterface` and `OutboxRelayInterface`, writing the core row and its `OutboxRow` in one storage call on every write and relaying the row at once; the relay dispatches on the row's `kind`, and an entity change appends the `Event` and publishes `ENTITY_CHANGED`, so the realtime channel has a producer |
| `storage/__init__.py`      | `<Ns>StorageInterface`, a docstring, no methods yet                     |
| `storage/impl/__init__.py` | empty                                                                   |
| `storage/impl/postgres.py` | `<Ns>StoragePostgresImpl(PgStorageBase, <Ns>StorageInterface)`; every method opens its session through the base's funnel, passing the call's scope (`org_id`, and `user_id` when the call narrows to one person), which is what sets the transaction settings the database policies read, as The Storage Layer (The Second Fence) states |
| `storage/impl/memory.py`   | `<Ns>StorageMemoryImpl(MemoryStorageBase, <Ns>StorageInterface)`        |
| `storage/tables/__init__.py` | empty; the entity skill adds one module per table                     |

## Changed

| File                                      | Change                                                            |
|-------------------------------------------|-------------------------------------------------------------------|
| `om/src/<root>/om/storage/root.py`         | `get_<ns_singular>_storage() -> <Ns>StorageInterface` on `StorageInterface` |
| `om/src/<root>/om/storage/impl/postgres.py` | constructs `<Ns>StoragePostgresImpl` and returns it from the getter |
| `om/src/<root>/om/storage/impl/memory.py`  | constructs `<Ns>StorageMemoryImpl` and returns it from the getter  |
| `om/src/<root>/om/root.py`                 | constructs `<Ns>ManagerImpl` and adds field `<ns>` to `Managers`    |
| `om/README.md`                             | a link to the namespace's README, and the namespace's nouns in the relations it names |
| `om/tests/unit/test_roots.py` (or the existing root test) | asserts the new getter and the new manager field         |
| `om/src/<root>/om/storage/roles.py`        | nothing yet: the namespace declares no table, and the entity skill adds the role and the tenancy scope of each one it creates |

## Procedure

1. Create the files in the table, then wire the roots.
2. A cross-manager dependency the new manager needs is a constructor
   parameter typed by interface and a wiring line in `root.py`; the
   interface stays untouched.
3. When a first entity was named, read
   `${CLAUDE_SKILL_DIR}/../arch-scaffold-entity/SKILL.md` and follow
   its Created, Changed, and Procedure with these arguments:
   `<namespace> <FirstEntity> <field:type ...> --role <role> --scope <scope>`.

## Output

As `${CLAUDE_SKILL_DIR}/../_shared/scaffold-conventions.md` states.
