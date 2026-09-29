---
name: arch-scaffold-new
description: "Start a new system in the guideline's shape: pick its name, copy the scaffold's domain-agnostic core under it, run its gates, record the product's first decisions, then add the first namespace."
allowed-tools: Read, Grep, Glob, Write, Edit, Bash(python3:*), Bash(make setup), Bash(make check), Bash(make openapi), Bash(make infra-up), Bash(make migrate), Bash(make migrate-check), Bash(make test-integration), Bash(uv run:*), Bash(uv sync:*), Bash(pnpm install:*), Bash(pnpm run:*), Bash(git status:*), Bash(git rev-parse:*), Bash(lsof:*), Bash(docker info:*)
---

# arch-scaffold-new

A path that starts with `../` is read from this skill's folder.
Conventions: `../_shared/scaffold-conventions.md`.
Sections of `../../architecture.md`: Monorepo
Folder Structure, Deployment (Local: Docker Compose, Twins for External
Services), Cross-Cutting Conventions (Records of Decisions).

The scaffold, `../../scaffold/acme_root/`, is the
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
  folder at once: one or two snake_case words, never a standard-library
  module, a Python keyword, or `acme`, and no longer than the bound
  `new.py` holds it to, `MAX_NAME_LENGTH` (17 characters). The bound
  keeps `<name-kebab>-production-api`, the API's target group, within
  the 32 characters AWS allows, so the name survives the first cloud
  deploy; `new.py` refuses a longer name and says the bound. With no
  name given, take the product's own short name when the spec or the
  description gives one. Else take the word of the product's name that
  says what the product is about, leaving out a word any product could
  carry, such as independent, platform, app, or system (`Independent
  Journalism Platform` gives `journalism`); two words only when one
  cannot say it. Ask only when the product names nothing.
- The folder is `<name>` in the current directory. It must not exist,
  or must be empty: an empty folder of that name is no collision, and
  the copy goes into it. Refuse when the current directory is inside a
  git repository (`git rev-parse --show-toplevel` answers there), since
  the copy starts a repository of its own.
- A namespace always arrives with an entity, since
  `om/tests/unit/test_interfaces.py` refuses an interface that declares
  no method. With no `--first`, the first namespace is the noun the
  product's first loop acts on, in the plural, and its entity is the
  first that loop names, with the fields the product gives it. With
  `--first` and no entity, the entity is that namespace's first in the
  product.
- `--codeowners` names the owners `.github/CODEOWNERS` lists, comma
  separated, each an account or an `org/team`. Without it, the file
  keeps the copy's placeholder team, and the output names the file as
  one for the person to set.

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
| `.github/CODEOWNERS` (with `--codeowners`) | on every rule line, the owners in place of the placeholder team |

## Procedure

1. Settle the name and the folder, and refuse as the Input states.
2. Copy: `python3 <new.py> <name>`, where `<new.py>` is the absolute
   path of `../../scaffold/new.py`.
   Then, in the folder, `make setup` and `make check`. The copy is green
   before this skill writes anything, so a gate that fails here is a
   defect of the scaffold: stop with the cause pre-existing, name the
   gate, and change nothing.
3. Tell whether Docker runs: `docker info` exits 0 when it does. When
   it runs, settle the local ports before any stack starts. Copy
   `.env.example` whole to `.env`, as `make up` does. Then check every
   port knob in it with `lsof -i :<port>`: the compose stack's
   (`<NAME>_POSTGRES_PORT` and the rest) and the host processes'
   (`<NAME>_PORT`, `<NAME>_METRICS_PORT`, `<NAME>_COLLECTOR_SCRAPE_PORT`,
   and any other `_PORT` knob the file holds). For each that is taken,
   set a free one in `.env`, with every URL knob that names it, and
   never stop what holds it. The compose project is the product's name;
   `COMPOSE_PROJECT_NAME` in `.env` sets another.
   When Docker does not run, the database targets of this skill and of
   the skills it follows (`make infra-up`, `make migrate`,
   `make migrate-check`, `make test-integration`) are skipped, and the
   output names each one skipped; every other gate runs.
4. Record the product's first decisions as ADRs, in the shape of the
   copy's own:
   - one for the product on the core: what an org, a member, and an
     operator are in the product, which of the core's pieces it uses
     (files, orchestrations, the work queue), and its first namespaces;
   - one per outside provider the product names beyond the identity
     provider the core has: the integration under `integrations/` that
     will reach it, and the twin that stands in wherever no account is
     configured. The integrations come with the namespace that needs
     them.

   Then write the opening of `README.md` and the summary of `llms.txt`.
   With `--codeowners`, write the owners into `.github/CODEOWNERS`: on
   every line that is not a comment, the owners, each with a leading
   `@` (`acme` gives `@acme`, `acme/infra` gives `@acme/infra`) and
   separated by a space, take the place of the copy's placeholder team.
   The paths stay as they are.
5. Read `../arch-scaffold-namespace/SKILL.md` and
   follow it with the first namespace, its entity, and the entity's
   fields. Their gates are not run there: step 6 runs them once, for
   all three skills.
6. Run the gates once, in the order the conventions' After writing
   gives, then `make test-integration` when Docker runs, as CI runs it
   on a copy. `make openapi` runs once in all: the entity skill runs it
   before its screen, so it does not run again here.

A gate of steps 5 and 6 that fails on what this skill wrote is fixed,
and its step runs again from its first command, as After writing
states: the first run plus at most 3 reruns.

## Output

As `../_shared/scaffold-conventions.md` states,
with these differences. The name and where it came from come first.
The copy is one line, the one `new.py` printed (its file count, the
folder, the name, and the pin), in place of its files: nothing is
committed, so `git status` names every copied file. After it, each file
this skill and the skills it followed wrote or changed, one per line,
as they wrote them; then the ADRs by number, and the database gates
skipped when Docker did not run. Then `The tree is uncommitted, and the
first commit is the person's.` A `Stopped:` line, when there is one,
still closes the output.
