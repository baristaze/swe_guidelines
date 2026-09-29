---
name: arch-scaffold-namespace
description: "Create an object-model namespace (a swimlane) with its first entity: the manager interface and impl, storage in Postgres and memory, its README, and the wiring into both roots, in the shape of the scaffold's own namespaces. Python."
allowed-tools: Read, Grep, Glob, Write, Edit, Bash(make check), Bash(make infra-up), Bash(make migrate), Bash(make migrate-check), Bash(make openapi), Bash(docker info:*), Bash(uv run:*), Bash(git status:*)
---

# arch-scaffold-namespace

A path that starts with `../` is read from this skill's folder.
Conventions: `../_shared/scaffold-conventions.md`.
Sections of `../../architecture.md`: Namespaces as
Swimlanes, Interfaces (Injectability), The Business Layer
(Cross-Manager Dependencies), The Storage Layer (Namespace Shape,
Storage Root, Cross-Storage Dependencies), Documentation as Code (A
README at Every Level).

## Input

`<namespace> <FirstEntity> [field:type ...] [--role core|activity|queue|admin] [--scope system|org|identity|both]`

Example: `inventory Warehouse address:str timezone:str`. Both are
required; ask for them when missing. A namespace arrives with its first
entity, since `om/tests/unit/test_interfaces.py` refuses an interface
that declares no method. The entity arguments, the role, and the scope
go to `arch-scaffold-entity` in step 3.

## Created

The shape is the `media` namespace, `om/src/<name>/om/media/`. Under
`om/src/<name>/om/<ns>/`:

| File | Holds |
|------|-------|
| `__init__.py` | `from .manager import <Ns>ManagerInterface` |
| `README.md` | the namespace in the product's language, one level below `om/README.md`: its nouns, what can happen to them, the rules that hold; no developer or operator instruction |
| `manager.py` | `<Ns>ManagerInterface`, a docstring naming the swimlane |
| `impl/manager.py` | `<Ns>ManagerImpl`, taking its storage interface, the `OutboxRelayInterface`, and every manager it calls, each by interface |
| `storage/__init__.py` | `<Ns>StorageInterface`, a docstring saying every operation takes `org_id` first |
| `storage/impl/postgres.py` | `<Ns>StoragePostgresImpl(PgStorageBase, <Ns>StorageInterface)` |
| `storage/impl/memory.py` | `<Ns>StorageMemoryImpl(MemoryStorageBase, <Ns>StorageInterface)` |
| `types/__init__.py`, `impl/__init__.py`, `storage/impl/__init__.py`, `storage/tables/__init__.py` | empty; the entity skill fills `types/` and `storage/tables/` |

## Changed

| File | Change |
|------|--------|
| `om/src/<name>/om/storage/root.py` | `get_<ns_singular>_storage()` on the storage root |
| `om/src/<name>/om/storage/impl/postgres.py`, `memory.py` | each root builds its impl and returns it from the getter |
| `om/src/<name>/om/root.py` | `<Ns>ManagerImpl` built in `build_managers`, and the field `<ns>` on `Managers` |
| `om/tests/unit/test_roots.py` | the new getter and the new field held |
| `om/README.md` | a link to the namespace's README, and its nouns in the relations the page names |

## Procedure

1. Write the files, then wire both storage roots and `build_managers`,
   as `media` is wired. A manager this one calls is a constructor
   parameter typed by its interface, and `build_managers` builds the
   callee first.
2. The namespace reaches the API with its first entity, whose skill
   adds the service, its impl, and its router.
3. Read `../arch-scaffold-entity/SKILL.md` and
   follow it with `<namespace> <FirstEntity> <field:type ...> --role
   <role> --scope <scope>`, before the gates run.

## Output

As `../_shared/scaffold-conventions.md` states.
