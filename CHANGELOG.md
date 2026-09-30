# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.46.0 (2026-09-30)

The count of a manager interface's operations is where a review looks,
never a limit, and the gateway's log filter drops a request's target in
any form. Minor, with one reversal in part, named below.

A count cannot tell one coherent duty of 23 operations from a grab bag
of 23. A limit makes an agent split what belongs together, or park an
operation where it does not belong, to pass a gate. So the number says
where to read, and a review says what to do.

### Changed

- Reversed in part: CON-01 and The Business Layer no longer bound a
  manager interface. An interface past twenty operations, or the
  project's own number, is read for a split: it delegates a duty its
  callers use apart, and stays whole when its operations are one duty.
  A review reports one left whole with its count and the duty that
  holds it together.
- `arch-check` fails no build by the count. Past the threshold it names
  the interface and its count under the rule's `to_judge`, in the JSON
  report and as a `to judge:` line in the text report, and the exit
  status does not read it. The option is `review_threshold` under
  `[tool.arch-check.options.CON-01]`; `max_operations` is read as the
  same key, and a table that sets both is refused.
- The review skills judge every `to_judge` entry in scope, whether or
  not the lens has a checker finding, and a report holds a place judged
  no breach. `arch-review-full` hands each reviewer its rules' entries
  whole.
- `arch-deviate` writes a checker entry only for a finding the checker
  reports, and records nothing where the rule itself allows what is
  described. The scaffold conventions say how a scaffold decides on a
  delegate: by the request's own words for the duty and the callers the
  run writes.

### Fixed

- The gateway's log filter finds a request's target by its place in the
  line and writes a dash there, whatever the target's form. In 0.45.0 a
  target in absolute form, or with no leading slash, reached uvicorn's
  lines with its path and its query string.

### What a copy does

- A copy that recorded an exception or an inline ignore for the count
  under 0.44.0 or 0.45.0 removes it: it matches no finding now, and the
  checker reports it as stale until it goes. A copy that set
  `max_operations` changes nothing.
