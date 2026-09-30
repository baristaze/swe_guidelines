# ADR 0001: The root package is `acme`

**Status**: accepted (2026-09-28)

## Context

Every Python distribution in the monorepo shares one import root, so a
namespace reads as `<root>.om.<ns>`, `<root>.infra.<capability>`, and
`<root>.services.<svc>`. The guideline warns that a root named
`platform` shadows the standard-library module of the same name.

## Decision

The root package is `acme`, the product's name. It is a namespace
package: no distribution has `acme/__init__.py`, so `acme-om`,
`acme-infra`, and every service and worker add subpackages to the same
root.

## Consequences

Imports read `from acme.om.base import Platform`. Every new distribution
uses the `src/acme/...` layout and never adds `acme/__init__.py`.
