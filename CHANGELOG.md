# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.47.0 (2026-09-30)

A copy's write token lives apart from what a reading skill holds, and no
tenant's words leave the process in an exception's text or an outbound
call's query. Minor, with one reversal in part, named below.

The boundary of what an agent can do is the credential it holds, never
the prompt. A read skill that sources the file holding a write token has
only its prompt between it and a write, and a tenant's words that reach
it by a log line or a tracker event can steer it.

### Changed

- Reversed in part: CTX-38 held the `write` token in the ops env file; a
  `write` token there is now its violation. The provisioner's token
  lives in `~/.config/acme/ops/<env>.provisioner.env`, read only by
  `acme-ops traffic` and `stress`. The env file a read skill sources
  holds the `read` token alone, the read skills pre-approve only the
  `acme-ops` read commands they run, and OPS-09 and Operator
  Credentials say so.
- An exception's text leaves the process only when the platform raised
  it as a server error: the JSON log line, the tracker's event, and a
  work item's, outbox row's, or orchestration's failure record keep the
  exception's type and its frames. An error logged by its text alone
  now carries its frames.
- An outbound call's breadcrumb keeps its method, its status, and its
  URL's scheme, host, and path, never its query. The HTTP clients' own
  loggers (`httpx`, `httpcore`, `urllib3`) write from WARNING up, since
  below it they write a request's whole URL.
- `ops-root-cause` names each read it makes, with a `jq` that keeps
  what the step needs and never an exception's text; it reads the
  tracker by request id for its environment, and every read by the
  window's bounds.

### Fixed

- The scaffold's `.gitignore` no longer ignores a namespace named
  `reports`, `coverage`, `build`, or `dist`: its build and coverage
  folders are anchored where their tools write them.
- The worker loop's tests wait on what they assert, not on a second of
  the wall clock, so a loaded runner no longer fails them.
- The benchmark's redactor, and `make runs` through it, replace
  Stripe's secret, restricted, and webhook signing keys.

### What a copy does

- Move the provisioner's token out of the ops env file: run the line
  the first `acme-ops` command prints, or delete
  `ACME_PROVISIONER_TOKEN` from the env file and run
  `uv run acme-ops token --env <env> --identity provisioner`.
- A copy's own code that logs an exception by its text (`%r`, `%s`)
  keeps it only in the plain format a developer reads; pass
  `exc_info=` to keep its frames in the JSON line.
