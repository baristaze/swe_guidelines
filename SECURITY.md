# Security Policy

This repository contains documentation, Markdown lens catalogs, skill
definitions, and small standard-library Python scripts that run in CI.
It ships no service and no network listener, and the scripts import
nothing past the standard library. Three tools are fetched at pinned
versions to run the gate, none of them shipped: `pytest`, for the
scripts' own tests; `markdownlint-cli2`, which `make lint` fetches
through `npx`; and `@anthropic-ai/claude-code`, which CI installs so
`make plugin` can validate the manifests.

## Reporting a vulnerability

If you believe you have found a security issue in this repository (for
example a script that could be made to execute untrusted input, or a
skill that instructs an assistant to take an unsafe action), please do
not open a public issue. Use the repository's private vulnerability
reporting ("Report a vulnerability" under the Security tab) so the
report reaches the maintainer directly.

You can expect an acknowledgement within a week and a fix or a written
assessment within thirty days.

## Scope

In scope:

- The scripts under `scripts/` and the `Makefile`.
- The CI workflow under `.github/workflows/`.
- Skill instructions under `skills/` that could lead an assistant to
  run a destructive command, exfiltrate data, or write outside the
  repository it was invoked in.

Out of scope:

- The design recommendations in `architecture.md` themselves. They are
  opinions about how to build systems; a disagreement with a
  recommendation is an issue, not a vulnerability.
