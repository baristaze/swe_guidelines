---
name: arch-scaffold-new
description: "Start a new system in the guideline's shape: pick its name, copy the scaffold's domain-agnostic core under it, run its gates, record the product's first decisions, then add the first namespace."
allowed-tools: Read, Grep, Glob, Write, Edit, Bash(python3:*), Bash(make setup), Bash(make check), Bash(make openapi), Bash(make infra-up), Bash(make migrate), Bash(make migrate-check), Bash(make test-integration), Bash(uv run:*), Bash(uv sync:*), Bash(pnpm install:*), Bash(pnpm run:*), Bash(git status:*), Bash(git rev-parse:*), Bash(lsof:*)
---

# arch-scaffold-new

Conventions: `${CLAUDE_SKILL_DIR}/../_shared/scaffold-conventions.md`.
Sections of `${CLAUDE_SKILL_DIR}/../../architecture.md`: Monorepo
Folder Structure, Deployment (Local: Docker Compose, Twins for External
Services), Cross-Cutting Conventions (Records of Decisions).

The scaffold, `${CLAUDE_SKILL_DIR}/../../scaffold/acme_root/`, is the
domain-agnostic core of a system in the guideline's shape, whole and
green. This skill copies it under the product's name, then adds the
product: its first decisions and its first namespace. It writes no part
of the core by hand.

## Input

`[<name>] [--first <namespace> [<Entity> [field:type ...]]] [--codeowners <owner,...>]`,
and the product: the path of its spec, or a description, in the
arguments or in the conversation.

Example: `free_journalism --first journalists Journalist display_name:str`.

- `<name>` is the project's code name, its Python package, and its
  folder at once: one or two snake_case words of at most 17 characters,
  never a standard-library module, a Python keyword, or `acme`. The
  bound keeps `<name-kebab>-production-api`, the longest load balancer
  target group name the copy's Terraform makes, within AWS's 32
  characters, so the name survives the first cloud deploy. With no name
  given, take it from the product's own name in the spec or the
  description, in at most two words, leaving out a word any product
  could carry, such as platform, app, or system (`Free Journalism
  Platform` gives `free_journalism`). When that name is longer than 17
  characters, take a shorter one the product gives: its short name, one
  of its two words, or an abbreviation of them. Ask only when the
  product names nothing.
- The folder is `<name>` in the current directory. It must not exist,
  or must be empty. Refuse when the current directory is inside a git
  repository (`git rev-parse --show-toplevel` answers there), since the
  copy starts a repository of its own.
- A namespace always arrives with an entity, since
  `om/tests/unit/test_interfaces.py` refuses an interface that declares
  no method. With no `--first`, the first namespace is the noun the
  product's first loop acts on, in the plural, and its entity is the
  first that loop names, with the fields the product gives it. With
  `--first` and no entity, the entity is that namespace's first in the
  product.
- `--codeowners` names the owners `.github/CODEOWNERS` lists. Without
  it, the file keeps the copy's placeholder team, and the output says
  so.

## Created

| File | Holds |
|------|-------|
| `<name>/` | the scaffold, copied by `new.py` under the name in each of its forms, pinned at this plugin's release, in a new git repository with nothing staged |
| `<name>/docs/adr/<n>-*.md` | the product's first decisions, numbered from one above the highest ADR there |
| the first namespace and its entity | as `arch-scaffold-namespace` and `arch-scaffold-entity` create them |

## Changed

| File | Change |
|------|--------|
| `README.md`, `llms.txt` | the opening and the summary say what the product is, in place of the core's description |
| `.github/CODEOWNERS` (with `--codeowners`) | the owners |

## Procedure

1. Settle the name and the folder, and refuse as the Input states.
2. Copy: `python3 ${CLAUDE_SKILL_DIR}/../../scaffold/new.py <name>`.
   Then, in the folder, `make setup` and `make check`. The copy is green
   before this skill writes anything, so a gate that fails here is a
   defect of the scaffold: stop with the cause pre-existing, name the
   gate, and change nothing.
3. Record the product's first decisions, one ADR each, in the shape of
   the copy's own ADRs:
   - the product on the core: what an org, a member, and an operator
     are in the product, which of the core's pieces it uses (files,
     orchestrations, the work queue), and its first namespaces;
   - the outside providers: each one the product names beyond the
     identity provider the core has, the integration under
     `integrations/` that will reach it, and the twin that stands in
     wherever no account is configured. The integrations come with the
     namespace that needs them.

   Then write the opening of `README.md` and the summary of `llms.txt`.
4. Read `${CLAUDE_SKILL_DIR}/../arch-scaffold-namespace/SKILL.md` and
   follow it with the first namespace, its entity, and the entity's
   fields.
5. Run `make openapi` when a route was added, then `make check`. When
   Docker runs, the integration suite follows, as CI runs it on a copy.
   The local stack's host ports are knobs, `<NAME>_<SERVICE>_PORT`, and
   `.env.example` lists them with the URL knobs that name them. Check
   each with `lsof -i :<port>`. For each that is taken, set a free one
   in `.env`, with every URL knob that names it, and never stop what
   holds it. Then `make infra-up`, `make migrate`, `make migrate-check`,
   and `make test-integration`.

A gate of steps 4 and 5 that fails on what this skill wrote is fixed,
and its step runs again from its first command, as After writing
states: the first run plus at most 3 reruns.

## Output

As `${CLAUDE_SKILL_DIR}/../_shared/scaffold-conventions.md` states,
with the name and where it came from first, the ADRs by number after
the files, and then `The tree is uncommitted, and the first commit is
the person's.` A `Stopped:` line, when there is one, still closes the
output.
