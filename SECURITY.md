# Security Policy

This repository contains documentation, Markdown lens catalogs, skill
definitions, a static checker (`checkers/`), a benchmark harness
(`benchmark/`), and small Python scripts that run in CI. Everything
imports the standard library alone at import time; the harness imports
the provider SDKs, `pyyaml`, `jsonschema`, and `websockets` inside the
functions that use them, and only a benchmark run, which is started by
hand and never on push, calls a provider. The one network listener is
`benchmark/serve.py`, a local viewer of run folders bound to the
loopback address by default. The gate fetches tools at pinned versions
and ships none of them: `pytest`, `pyyaml`, and `jsonschema` through
`uv` for the tests; `ruff` and `mypy` through `uvx`; and
`markdownlint-cli2` through `npx`. The gate never fetches
`@anthropic-ai/claude-code`. `make plugin` runs it only when `claude`
is already installed, and the CI workflow installs it at a pinned
version so the manifests are validated there.

## Reporting a vulnerability

If you believe you have found a security issue in this repository (for
example a script that could be made to execute untrusted input, or a
skill that instructs an assistant to take an unsafe action), please do
not open a public issue. Report it through the repository's private
vulnerability reporting, so the report reaches the maintainer directly:
open <https://github.com/baristaze/swe_guidelines/security/advisories/new>,
or "Report a vulnerability" under the Security tab. The new-issue page
links to the same form.

You can expect an acknowledgement within a week and a fix or a written
assessment within thirty days.

## Scope

In scope:

- The scripts under `scripts/`, the checker under `checkers/`, the
  harness under `benchmark/`, and the `Makefile`.
- The workflows under `.github/workflows/`.
- Skill instructions under `skills/` that could lead an assistant to
  run a destructive command, exfiltrate data, or write outside the
  repository it was invoked in.

Out of scope:

- The design recommendations in `architecture.md` themselves. They are
  opinions about how to build systems; a disagreement with a
  recommendation is an issue, not a vulnerability.
