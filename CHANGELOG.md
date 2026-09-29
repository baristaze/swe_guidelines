# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.38.0 (2026-09-29)

Every loop an agent runs has a count, and the benchmark measures a
whole system. A scaffold that fixes a gate and runs it again, and an
ops skill that watches, polls, or follows a request, now stops at a
stated count and says what it does there. Money and time still bound
each one first, and the count sits well above real use. The benchmark
gains agentic judges that read the output against the guideline and
against its reference implementation, a scenario that builds a system
from a product spec in phases on a virtual machine, and the runs of
every scenario, checked in on a page per scenario. Minor: rules are
added and sharpened, and none is reversed.

### Changed

- `skills/_shared/scaffold-conventions.md`, After writing: a gate that fails on what a
  scaffold wrote is fixed, and its step's commands run again from the
  first, at most 3 reruns. When the count runs out, the scaffold stops
  and keeps its fixes. A failure that was there before, a command that
  fails on the machine, and a fix that would need an exception to a
  rule stop at once. `arch-scaffold-new`, `arch-upgrade-deps`, and
  `arch-new-aspect` follow them, and the output ends with
  `Stopped: <command>: <what went wrong>; <cause>`.
- "Operations, Operational Skills": `ops-watch` runs at most 30
  batches of 30 seconds to five minutes, each with at most 20 tool
  calls. `ops-root-cause` follows at most 5 request ids, one pass
  each. A Logs Insights query is polled at most 10 times. A session
  follows at most 2 hops of a report's Next, and the Next of
  `ops-cloud-deployment-create` and `stress-test-create-or-update` is
  the person's to run. A tree that copied the templates on 0.37.0
  takes the same bounds.
- `agents/arch-reviewer.md` caps its turns at 80.
- `run.py --out` names the runs root, and a run folder goes in
  `<out>/<scenario>/`. A resume and a judge-again write beside their
  source.

### Added

- `make skills` refuses a skill that fixes and reruns without saying
  `at most <n> reruns`, and `make agents` refuses an agent with no turn
  cap.
- Agentic judges. Each provider's judge reads the output with
  read-only tools over named roots, scores it against the guideline
  and against the guideline's reference implementation, and names the
  gaps behind each score. The harness weighs the two 0.4 and 0.6. A
  judge has a budget in tokens and in dollars, and gets a last turn to
  submit before it would pass either.
- A skill subject runs in phases, each a fresh session bounded by money
  and time, and keeps each phase's milestone. `run.py resume` starts a
  new run after a phase, `run.py judge` judges an archived output
  again, and a resume after the last phase runs only the judges named
  and carries the rest. A chain has one line: a run resumes from its
  newest folder, which reaches every milestone before it.
- `create-full-system`: the scaffold skills build `free-journalism`
  from its product spec, the scaffold and then the MVP. With
  `--with extras`, a standalone review reads the tree and a last phase
  closes its high findings. The extras are an opt-in.
- The vm runtime, on a Lima machine: it stages the plugin and the
  target, hands the subject its key through a file, mounts nothing of
  the host, and removes the subject's containers, networks, and
  volumes after each repeat. The subject runs `arch-check` from the
  staged plugin, so it never fetches this repository at a tag.
- `run.py --preflight` checks what a run needs before it spends
  anything, and `run.py --rehearsal` runs a scenario end to end with
  every bound cut small, for at most $5.
- A run records what it spent, in tokens and dollars, the subject's
  counted across every agent its session ran, and the versions it ran: the checkout,
  Claude Code, the image, and the target. A phase whose session wrote
  no result is a lower bound, and the run's spend cap counts it at its
  phase's cap.
- Checked-in runs live in `benchmark/runs/<scenario>/`, on a page per
  scenario: what its subject does, how it is scored, and its runs. A
  run and its resumes are one row, and its cost is the chain's total.
  `make runs` holds the pages to the run folders, and refuses a
  rehearsal, a run whose subject named the benchmark's run folders, and
  a file holding a key or a provider's account id. `run.py redact`
  replaces those ids and a rate limit's figures.
- `arch-benchmark-browser` asks each site as a senior architect judging
  the material only, adds grok.com as its fourth site, and says what a
  session that did not run records and how a retry counts its polls.

### Fixed

- A judge and a qa subject time out, are asked at most twice, and get
  no token limit below the model's maximum. A judge waits out a
  per-minute rate limit and asks again, and its fallback names the
  first model's last error.
- An Agent call that returns its helper's hand-back counts as
  answered, so a phase whose helpers all answered is not ended early.
- The subject's cost estimate prices Claude Opus 5.5's cache hits at
  $0.20 per million tokens.
