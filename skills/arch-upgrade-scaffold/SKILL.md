---
name: arch-upgrade-scaffold
description: "Move a copy of the scaffold, or a layer whose own scaffold builds on it, to a later release by merging it. The scaffold branch holds the scaffold as the repository took it, and a three-way merge brings in what changed since. The first run grafts a base at the release the repository pins, or takes a layer's first scaffold."
allowed-tools: Read, Grep, Glob, Edit, Write, WebFetch, Bash(python3:*), Bash(git status:*), Bash(git fetch:*), Bash(git ls-remote:*), Bash(git rev-parse:*), Bash(git symbolic-ref:*), Bash(git switch:*), Bash(git log:*), Bash(git show:*), Bash(git diff:*), Bash(git merge:*), Bash(git merge-base:*), Bash(git checkout:*), Bash(git rm:*), Bash(git add:*), Bash(git commit:*), Bash(git ls-files:*), Bash(git ls-tree:*), Bash(grep:*), Bash(uv lock:*), Bash(pnpm install:*), Bash(docker info:*), Bash(make setup), Bash(make check), Bash(make openapi), Bash(make infra-up), Bash(make migrate), Bash(make migrate-check), Bash(make test-integration)
---

# arch-upgrade-scaffold

A path that starts with `../` is read from this skill's folder as
`realpath` resolves it.

A copy of the scaffold keeps its base in git. Its `scaffold` branch
holds the scaffold as the copy took it: each commit there is the
scaffold at one commit of its source, renamed to the copy's name by
that commit's own `new.py`, and its parent is the render before it.
`../../scaffold/base.py` writes those commits; its docstring holds the
details. The copy's main branch merges the `scaffold` branch, so the
render it merged last is the merge base of the next move. `git merge
scaffold` then brings in what the scaffold changed since, three ways,
and keeps what the copy changed.

A layer is a repository whose own scaffold builds on its source's. It
holds the source's `scaffold/` folder at `scaffold/`, under the name
`acme`, and changes and adds to it there. A product is copied from a
layer as it is from this guideline. A layer takes its source's
`scaffold/` folder unchanged: each commit on its `scaffold` branch is
that folder at one commit of the source, written by `base.py --layer`,
and the layer merges the branch as a copy does. The source of a copy or
a layer is this guideline, or a layer built on it.

This skill makes one move on a work branch, commits it, and pushes
nothing. The pull request that carries it merges with a merge commit,
never a squash: a squash drops the parent that makes the base, and the
next move would merge against an older one.

## Input

`[<ref>] [--name <name> | --layer] [--source <url>] [--tarball <file>]`

- `<ref>`: the release, branch, or commit of the source to move to.
  Without one, the release after the one the checkout pins (`pinned at
  release` in `<root>/specs/architecture.md`): the lowest `vX.Y.Z` tag
  above the pin in
  `git ls-remote --tags --refs --sort=v:refname https://github.com/baristaze/swe_guidelines`,
  which lists every release in version order, so `v0.10.0` follows
  `v0.9.0`. A move takes one release, as the pin
  moves: when releases lie between the pin and a target named, the
  target is the first of them, and each next one is a move of its own.
  When no tag is above the pin, stop: there is nothing newer to take.
  When the source, given or the one the base records, is not this
  guideline, `<ref>` is required: the pin names this guideline's
  release, never the source's. A layer's
  first take, before it holds `scaffold/`, has no pin: without `<ref>`,
  it takes the last tag of that listing, the newest release.
- `--name`: the copy's name, as `new.py` took it. Without one,
  `base.py` reads the name the base recorded, else the `package` under
  `[tool.arch-check]` in the copy's root `pyproject.toml`. A layer takes
  no name.
- `--layer`: the checkout is a layer. It is one also when it holds
  `scaffold/acme_root/` and `scaffold/new.py`, or when its base records
  `Scaffold-Name: acme`. A layer's first take, before it holds
  `scaffold/`, needs the flag.
- `--source`: the source on GitHub, `https://github.com/<owner>/<repo>`,
  when it is a layer rather than this guideline. Without it, `base.py`
  takes the source the base records (`Scaffold-Source`), and it refuses
  a `--source` that names another. It reads a private source with the
  token `gh auth token` gives.
- `--tarball`: the source's tarball at `<ref>`, for a private source
  with no token that reads it. Where the source can be read,
  `gh api repos/<owner>/<repo>/tarball/<ref> > <file>` writes one.

It runs at the root of the checkout. `<base.py>` below is the absolute
path of `../../scaffold/base.py`, and `<flags>` what every run of it
carries: `--layer` in a layer, and `--name` and `--source` as given.
`<root>` is where the scaffold's own tree sits: the checkout's root in
a copy, `scaffold/acme_root/` in a layer. In a layer, `scaffold` names
a branch and a folder both, so a git command that takes paths ends its
revisions with `--`.

## Procedure

1. **Start clean.** `git status --porcelain` prints nothing, or refuse:
   the move's commits hold the move alone. `git fetch origin`. The main
   branch is the one `git symbolic-ref --short refs/remotes/origin/HEAD`
   names. Work on a branch cut from it as it is on origin: when the
   checkout is on the main branch, cut one,
   `git switch --no-track -c scaffold-<target> origin/<main>`, with the
   target's dots as hyphens (`scaffold-v0-41-0`), so that a push never
   lands on the main branch. On another branch, work there only when
   `git log --oneline origin/<main>..HEAD` prints nothing; otherwise
   refuse, since the move's commits hold the move alone.
2. **Find the base**: the last render the branch holds,
   `git log -1 --no-merges -E --grep='^Scaffold-Commit: [0-9a-f]{40}$' --format=%H HEAD`.
   When it answers, the checkout has a base, and its message names the
   source commit it took (`Scaffold-Commit`) and the name
   (`Scaffold-Name`). When it answers nothing but `scaffold` or
   `origin/scaffold` exists, the main branch has not merged its base:
   stop and say so. When a move's pull request is open, it merges
   first. Otherwise a move was squashed or dropped, and the person
   records the base the main branch holds: the render of the release it
   pins, which `git log --format='%H %s' <branch> --` names (`scaffold`, or
   `origin/scaffold` in a clone that has no local one), merged with
   `git merge -s ours --allow-unrelated-histories <that render>`, which
   changes no file. Then the skill runs again. When nothing answers, the
   checkout has no base.
3. **Graft, only when there is no base.** A layer that holds no
   `scaffold/` yet has nothing to graft: its first take is the merge of
   step 6, so go to step 4. Otherwise the checkout was made before its
   base was recorded, so the base is the release it pins: the version in
   `pinned at release` in `<root>/specs/architecture.md`. With a
   `--source` other than this guideline, the pin is not the source's
   ref: stop and say so. Render the pin,
   `python3 <base.py> v<pinned> <flags>`, then merge it without changing
   a file:
   `git merge -s ours --allow-unrelated-histories scaffold -m "<Name> is based on the scaffold at v<pinned>"`.
   `git diff --stat HEAD^1 HEAD` prints nothing. The merge says what the
   checkout is: the scaffold at its pin, and what it changed since.
   From here on, every difference between the two is its own, and the
   next merge keeps it. When `base.py` refuses because the
   release has no `scaffold/acme_root/`, stop: that release predates the
   scaffold, so the checkout was not made from it, and this skill does not
   apply. When
   the pin is the target, the graft is the whole move: go to step 8.
4. **Render the target.** `python3 <base.py> <ref> <flags>`, with
   `--tarball <file>` when given. It prints the new head of `scaffold`
   and the source commit it holds. When it refuses a private source and
   names `--tarball`, stop and say what it printed. When it prints
   that `scaffold` is unchanged, the render is already there: when
   `git merge-base --is-ancestor scaffold HEAD` exits 0, the branch
   holds it, so stop, with nothing to merge; otherwise go on and merge
   it. When it refuses because two renders are on two lines, stop and
   say so: the person keeps on `scaffold` the one the main branch
   merged or will merge.
5. **Read what the releases ask.** The source's `CHANGELOG.md` at the
   target: `../../CHANGELOG.md` when the target is this plugin's release,
   else `https://raw.githubusercontent.com/<owner>/<repo>/<ref>/CHANGELOG.md`
   for the source `base.py` names, and for a private one
   `gh api -H 'Accept: application/vnd.github.raw' 'repos/<owner>/<repo>/contents/CHANGELOG.md?ref=<ref>'`.
   Read each entry after the base's release. Note each reversal, each
   rule that now holds one of the checkout's deviations (the Deviations
   table in `<root>/specs/architecture.md`), and each step an entry asks
   of a project. Step 7 carries them. A layer's first take has no base
   and no deviation, and skips this step.
6. **Merge.** `git merge --no-ff --no-commit scaffold`, and on a
   layer's first take
   `git merge --no-ff --no-commit --allow-unrelated-histories scaffold`,
   which adds `scaffold/` whole. List the
   conflicts, `git diff --name-only --diff-filter=U`, and resolve each
   by the table below. Then read against the same table the paths the
   merge added, `git diff --name-only --diff-filter=A HEAD`, and every
   path it changed under `docs/adr/`, the migrations, and the lockfiles,
   `git diff --name-only HEAD -- <root>/docs/adr <root>/om/migrations <root>/uv.lock <root>/pnpm-lock.yaml`: a
   clean merge can still bring a file, or a line, the checkout must not
   take. In a layer, the table's copy is the layer, every path it names
   lies under `<root>`, and a lockfile is checked and regenerates there
   (`uv lock --check --directory <root>`, `pnpm install --dir <root> --frozen-lockfile`;
   `uv lock --directory <root>`, `pnpm install --dir <root> --lockfile-only`).
   Commit
   with the subject `The scaffold base moves to <ref>` and a body that
   lists each conflict and how it was resolved, one line each.

   | Where | Resolution |
   |-------|------------|
   | A line both sides changed | The scaffold's change to the core, and the copy's product around it. When both changed it to the same effect in other words, keep the copy's line: its own tests may hold its wording. When the scaffold's change undoes a deviation the copy records (its Deviations table names the ADR), keep the copy's line. |
   | A file the copy deleted and the scaffold changed | It stays deleted (`git rm`). When the copy keeps its own file for the same thing, such as a screen its product replaced, carry into that file what the scaffold's change alters in behaviour. |
   | A file the scaffold deleted and the copy changed | Deleted, unless the copy's own code still imports or runs it. |
   | `docs/adr/` | The copy's record of its own decisions. An ADR the copy never changed takes the scaffold's change, as any file does. Into one the copy changed, no scaffold text comes, by a conflict or by a clean merge; only a rule the scaffold's change alters goes into the copy's ADR of that decision, when it has one. An ADR the scaffold removed, which the copy changed, is the copy's to remove: it goes once the ADR that now holds its decision says what the copy's said, and nothing in the copy cites its number. A copy numbers its own ADRs from 1001 and meets no clash; a copy numbered before may. A new scaffold ADR whose number the copy already uses takes the copy's next free number at 1001 or above, when its decision holds in the copy, and is removed when it does not. Each line the merge brings that cites its old number (a link, an `ADR-NNNN`, an `adr =` path; `git diff HEAD` shows them) names its new number, or drops the citation when the ADR was removed: `arch-check` cannot tell, since the old number still names a file, the copy's own. The copy's own citations of that number stay. On a later move, a scaffold change to that ADR goes into the file under its new number, and the citations of the old number that move brings are rewritten the same way. After the merge, no two ADRs share a number. |
   | A migration | The chain the copy's databases applied: an applied file is never edited, and a chain the copy folded stays as the copy folded it. A scaffold migration that is not in the copy's chain stays out, a fold of the scaffold's own chain included. When it changes a table the copy has, the copy writes its own migration for that change, on its chain's head. A layer applies no database: the scaffold's migrations come in as the scaffold has them, on the first take and on every move, a fold included. The layer's own migrations follow them: the first of each role's own chain has the scaffold's head as its `down_revision`, re-pointed to the new head when a move brings one, so each role keeps one head. |
   | `uv.lock`, `pnpm-lock.yaml` | Never merged by hand: take the copy's (`git checkout --ours <file>`), and regenerate after the manifests merge (`uv lock`, `pnpm install --lockfile-only`). A lockfile the merge left without a conflict stays as merged while its check passes (`uv lock --check`, `pnpm install --frozen-lockfile`); when a check fails, that lockfile regenerates from the merged one with the same commands, never from the copy's. |
   | What `make openapi` writes | Take either side, then run `make openapi`. |
   | The pin | Every pin names this guideline's release, never a layer's ref. In a copy of this guideline, every pin names the target: `specs/architecture.md` and the Makefile's `ARCH_CHECK`. `grep -rn "v<base release>"` finds nothing outside the ADRs and a changelog. In a copy of a layer, and in a layer, a pin takes the source's line: the release of this guideline the source took. |

7. **Carry what the releases ask** (step 5). A deviation whose rule now
   holds leaves the Deviations table, and a row that deviates in part
   keeps only that part. Its ADR says what still holds. Where the merge
   brought the scaffold's own change to that ADR, the ADR is done.
   Otherwise rewrite it in place to the part that still holds, or, when
   nothing does, remove it with every citation of its number; its
   status stays one date, and no ADR is renumbered. The move itself gets
   no ADR: the pin and the merge commit record it. Commit this apart
   from the merge.
8. **Run the gates.** In a layer, each command below runs in `<root>`
   (`make -C <root> <target>`), unless the layer's `AGENTS.md` names its
   gates otherwise, and `services/` is `<root>/services/`. `make setup`,
   then `make check`. When
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
  the source commit it holds. When this run grafted, the graft's
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
