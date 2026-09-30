---
name: arch-upgrade-scaffold
description: "Move a copy of the scaffold to a later release of the guideline by merging it. The copy's scaffold branch holds the scaffold as the copy took it, and a three-way merge brings in what changed since. The first run grafts a copy with no base at the release it pins."
allowed-tools: Read, Grep, Glob, Edit, Write, WebFetch, Bash(python3:*), Bash(git status:*), Bash(git fetch:*), Bash(git rev-parse:*), Bash(git symbolic-ref:*), Bash(git switch:*), Bash(git log:*), Bash(git show:*), Bash(git diff:*), Bash(git merge:*), Bash(git merge-base:*), Bash(git checkout:*), Bash(git rm:*), Bash(git add:*), Bash(git commit:*), Bash(git ls-files:*), Bash(git ls-tree:*), Bash(grep:*), Bash(uv lock:*), Bash(pnpm install:*), Bash(docker info:*), Bash(make setup), Bash(make check), Bash(make openapi), Bash(make infra-up), Bash(make migrate), Bash(make migrate-check), Bash(make test-integration)
---

# arch-upgrade-scaffold

A path that starts with `../` is read from this skill's folder as
`realpath` resolves it.

A copy of the scaffold keeps its base in git. Its `scaffold` branch
holds the scaffold as the copy took it: each commit there is the
scaffold at one commit of this guideline, renamed to the copy's name by
that commit's own `new.py`, and its parent is the render before it.
`../../scaffold/base.py` writes those commits; its docstring holds the
details. The copy's main branch merges the `scaffold` branch, so the
render it merged last is the merge base of the next move. `git merge
scaffold` then brings in what the scaffold changed since, three ways,
and keeps what the copy changed.

This skill makes one move on a work branch, commits it, and pushes
nothing. The pull request that carries it merges with a merge commit,
never a squash: a squash drops the parent that makes the base, and the
next move would merge against an older one.

## Input

`[<ref>] [--name <name>]`

- `<ref>`: the release, branch, or commit of this guideline to move to.
  Without one, this plugin's release: `v` and the `version` in
  `../../.claude-plugin/plugin.json`.
- `--name`: the copy's name, as `new.py` took it. Without one,
  `base.py` reads the name the base recorded, else the `package` under
  `[tool.arch-check]` in the copy's root `pyproject.toml`.

It runs at the root of the copy's checkout. `<base.py>` below is the
absolute path of `../../scaffold/base.py`.

## Procedure

1. **Start clean.** `git status --porcelain` prints nothing, or refuse:
   the move's commits hold the move alone. `git fetch origin`. The main
   branch is the one `git symbolic-ref --short refs/remotes/origin/HEAD`
   names. Work on a branch cut from it as it is on origin: when the
   checkout is on the main branch, cut one,
   `git switch --no-track -c scaffold-<target> origin/<main>`, with the
   target's dots as hyphens (`scaffold-v0-41-0`), so that a push never
   lands on the main branch.
2. **Find the base**: the last render the branch holds,
   `git log -1 --no-merges -E --grep='^Scaffold-Commit: [0-9a-f]{40}$' --format=%H HEAD`.
   When it answers, the copy has a base, and its message names the
   guideline commit it took (`Scaffold-Commit`) and the name
   (`Scaffold-Name`). When it answers nothing but `scaffold` or
   `origin/scaffold` exists, the main branch never merged its base (a
   move squashed, or one never merged): stop and say so. The person
   merges that branch's head with `git merge -s ours <it>`, which
   records it and changes no file, then runs this skill again. When
   nothing answers, the copy has no base.
3. **Graft, only when there is no base.** The copy was made before its
   base was recorded, so the base is the release it pins: the version in
   `pinned at release` in `specs/architecture.md`. Render it,
   `python3 <base.py> v<pinned>` (with `--name` when given), then merge
   it without changing a file:
   `git merge -s ours --allow-unrelated-histories scaffold -m "<Name> is based on the scaffold at v<pinned>"`.
   `git diff --stat HEAD^1 HEAD` prints nothing. The merge says what the
   copy is: the scaffold at its pin, and what the copy changed since.
   From here on, every difference between the two is the copy's own,
   and the next merge keeps it. When `base.py` refuses because the
   release has no `scaffold/acme_root/`, stop: that release predates the
   scaffold, so the copy was not made from it, and this skill does not
   apply. When
   the pin is the target, the graft is the whole move: go to step 8.
4. **Render the target.** `python3 <base.py> <ref>`. It prints the new
   head of `scaffold` and the guideline commit it holds. When it prints
   that `scaffold` is unchanged, the base already is the target: stop,
   with nothing to merge.
5. **Read what the releases ask.** The guideline's `CHANGELOG.md` at the
   target: `../../CHANGELOG.md` when the target is this plugin's release,
   else `https://raw.githubusercontent.com/<owner>/<repo>/<ref>/CHANGELOG.md`
   for the source `base.py` names. Read each entry after the base's
   release. Note each reversal, each rule that now holds one of the
   copy's deviations (the Deviations table in `specs/architecture.md`),
   and each step an entry asks of a project. Step 7 carries them.
6. **Merge.** `git merge --no-ff --no-commit scaffold`. List the
   conflicts, `git diff --name-only --diff-filter=U`, and resolve each
   by the table below. Then read the paths the merge added,
   `git diff --name-only --diff-filter=A HEAD`, against the same table:
   a clean merge can still bring a file the copy must not take. Commit
   with the subject `The scaffold base moves to <ref>` and a body that
   lists each conflict and how it was resolved, one line each.

   | Where | Resolution |
   |-------|------------|
   | A line both sides changed | The scaffold's change to the core, and the copy's product around it. When both changed it to the same effect in other words, keep the copy's line: its own tests may hold its wording. When the scaffold's change undoes a deviation the copy records (its Deviations table names the ADR), keep the copy's line. |
   | A file the copy deleted and the scaffold changed | It stays deleted (`git rm`). When the copy keeps its own file for the same thing, such as a screen its product replaced, carry into that file what the scaffold's change alters in behaviour. |
   | A file the scaffold deleted and the copy changed | Deleted, unless the copy's own code still imports or runs it. |
   | `docs/adr/` | The copy's record of its own decisions. An ADR the copy never changed takes the scaffold's change, as any file does. Into one the copy changed, no scaffold text comes, by a conflict or by a clean merge; only a rule the scaffold's change alters goes into the copy's ADR of that decision, when it has one. A new scaffold ADR whose number the copy already uses takes the copy's next free number, when its decision holds in the copy, and is removed when it does not. After the merge, no two ADRs share a number. |
   | A migration | History the copy's databases applied: never edited. A scaffold migration that is not in the copy's chain stays out. When it changes a table the copy has, the copy writes its own migration for that change, on its chain's head. |
   | `uv.lock`, `pnpm-lock.yaml` | Never merged by hand: take the copy's (`git checkout --ours <file>`), and regenerate after the manifests merge (`uv lock`, `pnpm install --lockfile-only`). |
   | What `make openapi` writes | Take either side, then run `make openapi`. |
   | The pin | Every pin names the target: `specs/architecture.md` and the Makefile's `ARCH_CHECK`. `grep -rn "v<base release>"` finds nothing outside the ADRs and a changelog. |

7. **Carry what the releases ask** (step 5). A deviation whose rule now
   holds leaves the Deviations table, and a row that deviates in part
   keeps only that part. Where the merge brought the scaffold's own
   change to that ADR, the ADR is done; otherwise its status says the
   release that retired it. Record the move itself the way the copy
   records a pin move: when its ADRs keep one for each release adopted,
   write the next one. Commit this apart from the merge.
8. **Run the gates.** `make setup`, then `make check`. When
   `docker info` exits 0, also `make infra-up`, `make migrate`,
   `make migrate-check`, and `make test-integration`; otherwise the
   output names each one skipped. `make setup` formats the Python, and
   what it changes is part of the move: commit it. When the merge
   changed a file under `services/`, also run `make openapi` and commit
   what it regenerates. A gate that fails is fixed, and the
   fix is its own commit. The gate then runs again from its first
   command: the first run plus at most 3 reruns, then stop and say which
   gate fails and why.

## Output

- The base before and after: each render's commit on `scaffold`, and
  the guideline commit it holds. When this run grafted, the graft's
  merge commit and the release it grafted.
- Each conflict and its resolution, one line each, and each scaffold
  file the copy keeps out, with the reason.
- What the releases asked (step 5), and what step 7 did about each.
- The gates and their results, and those skipped.
- The next step: push `scaffold` and the work branch, then open the pull
  request and merge it with a merge commit, never a squash. A branch
  under `scaffold/` on origin refuses the push of `scaffold`; whoever
  may removes or renames it first. The pull request carries the renders
  either way. After the
  merge, run `arch-review-full all` on the main branch, as
  `../../docs/adopting.md` asks of a pin move.
