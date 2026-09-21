---
name: arch-upgrade-deps
description: "Upgrade every dependency of the current repository to its latest stable release, the current active LTS line where one exists, as the Software Design and Architecture Guidelines prescribe: runtimes, workspace tools, container images, CI steps, Terraform engine versions, and locked libraries; then run the repository's gates and hold back any upgrade that breaks them. Use periodically, or when a review raises DEL-26."
allowed-tools: Read, Grep, Glob, Edit, WebFetch, Bash(make check), Bash(make infra-reset), Bash(make infra-up), Bash(make migrate), Bash(make migrate-check), Bash(make test-integration), Bash(uv lock:*), Bash(uv sync:*), Bash(uv tree:*), Bash(pnpm update:*), Bash(pnpm install:*), Bash(pnpm view:*), Bash(pnpm outdated:*), Bash(git status:*)
---

# arch-upgrade-deps

The guideline keeps every dependency on its latest stable release, the
current active LTS line where the technology publishes one (Technology
Choices and How to Override Them, Versions, in
`${CLAUDE_SKILL_DIR}/../../architecture.md`; lens `DEL-26` in
`${CLAUDE_SKILL_DIR}/../../lenses/delivery.md`). Releases keep coming,
so a project drifts unless something moves it. This skill moves it in
one change, proves the change with the repository's own gates, and
leaves the working tree for a person to review.

## Input

`[<dependency> ...] [--plan]`

Examples: empty (every dependency), `python node postgres`, `--plan`.
Names limit the upgrade to those dependencies. `--plan` stops after
the plan table and edits nothing. Nothing else is asked for.

## Procedure

1. Read the Versions subsection and `DEL-26` in full. Run
   `git status`; refuse when the working tree has uncommitted changes,
   so the diff this skill leaves holds only the upgrade.
2. Run `make check` (or the repository's equivalent fast gate) before
   editing anything. When it fails, report the failure as pre-existing
   and stop.
3. Inventory every declaration, in the places the Versions subsection
   names: `.python-version` and `requires-python` in every
   `pyproject.toml`; `.nvmrc`; `packageManager` and `engines` in every
   `package.json`; the `FROM` lines of every Dockerfile; the image tags
   of every compose file; runtime versions and action references in
   every CI workflow; engine and runtime versions in Terraform. One
   dependency declared in several places is one row with every place
   listed.
4. Resolve the target of each row from the maintainers' own release
   data, fetched now, never from memory: the Node release index
   (`https://nodejs.org/dist/index.json`, the newest entry whose `lts`
   is set), `https://endoflife.date/api/<product>.json` for runtimes,
   databases, and caches, `pnpm view <package> version` for npm
   packages, and the project's release page for an action or a tool.
   The target is the current active LTS release where the technology
   publishes an LTS line, and the newest stable release otherwise;
   never a pre-release, a release candidate, or a line past its end of
   life. A release is adopted once a patch release sits behind it,
   never the day it ships, as the Versions subsection states: when the
   newest release has no patch release behind it (a `.0`, or a patch
   published today), the target is the release before it, and the
   plan table says so in its Line column. A target that cannot be
   confirmed from a source is marked unconfirmed and left unchanged.
5. Print the plan table (see Output). A backing service in the local
   compose stack that moves a major (Postgres 17 to 18, say) keeps its
   data files in a volume the new engine cannot open, so its row says
   `make infra-reset` in the Line column, and the plan prints, under the
   table, that the validation recreates the local volumes. With
   `--plan`, stop here.
6. Edit every declaration of each row to its target, keeping the
   declaration's precision: a file that names a minor line
   (`3.14`) gets the new minor line, one that names an exact release
   (`24.21.0`) gets the exact release, an image tag keeps its variant
   suffix (`-alpine`, `-slim`). A managed-service engine in Terraform
   moves only to a version the provider offers; when that is not
   confirmable, the row is unconfirmed.
7. Upgrade the libraries in two passes, when the repository has those
   workspaces.

   First, within the ranges: `uv lock --upgrade` and `uv sync`, and
   `pnpm update --recursive` and `pnpm install`. Both keep to the range
   each manifest declares, so this pass moves no library across a
   major. Never run `pnpm update --latest` over the workspace: it
   ignores the ranges and moves every package past its major at once,
   which is the move the second pass makes one library at a time.

   Then the majors. List the libraries whose range holds them below a
   newer major: `pnpm outdated --recursive` for npm, and
   `uv tree --outdated --depth 1` for Python. Raise them one library
   at a time, never every range at once. For npm,
   `pnpm update --recursive --latest <name>` rewrites that one range.
   For Python, edit the range in each `pyproject.toml` that declares
   it, then run `uv lock --upgrade-package <name>` and `uv sync`. Run
   step 8 after each raise and before the next, so a failing gate
   names the major that broke it.

   Then hold each library to the same rule as a runtime: a resolved
   release with no patch release behind it is pinned back to the
   release before it in the lock
   (`uv lock --upgrade-package <name>==<release>`,
   `pnpm update --recursive <name>@<release>`), and the report names
   it under Held back with "no patch behind it" in place of a failing
   check.
8. Validate: `make check`; then, when Docker is available,
   `make infra-up` (`make infra-reset` in its place when the plan says
   a database moved a major, which recreates the dependency volumes and
   starts nothing else; `make reset` runs `make up`, which also seeds
   and starts the application on the host), `make migrate`,
   `make migrate-check`, and
   `make test-integration`, only against the local compose stack:
   refuse when the effective database URL (the environment, `.env`, or
   the settings default) is not a local address.
9. On a failure, find the row that causes it. Fix it in place when the
   fix is mechanical and named in that release's upgrade notes (a
   renamed setting, a moved import, a new required field in a config
   file). When the fix would change what the application does, revert
   that row's edits and its lock changes, mark it held back with the
   failing output, and run step 8 again. Stop after every remaining row
   passes.

Never commit. Never edit application behavior to fit an upgrade. A
substitute the project's technology-choices ADR records is upgraded
like the technology it replaces.

## Output

A short report, and nothing else:

```markdown
# Dependency upgrade

| Dependency | Declared in | From | To | Line | Source |
|------------|-------------|------|----|------|--------|
| Node | `.nvmrc`, `.github/workflows/ci.yml` | 22.11.0 | 24.21.0 | active LTS | nodejs.org release index |
| Postgres | `deployment/local/docker-compose.yml` | 16 | 18 | stable, `make infra-reset` | endoflife.date |

**Libraries.** <count of Python and npm packages moved, and every major-version move by name, in the order the caps were raised>
**Fixed.** <mechanical fixes made for an upgrade, one per line with the file>, or none
**Held back.** <dependency, target, and the failing check, or "no patch behind it">, or none
**Unconfirmed.** <dependency and why no source confirmed a target>, or none
**Gates.** `make check` <passed | failed: what>; integration <passed | failed: what | skipped: no Docker>
```

The tree is uncommitted, and the commit is the user's.
