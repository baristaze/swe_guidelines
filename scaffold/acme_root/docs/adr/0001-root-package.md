# ADR 0001: The root package is `acme`

Date: 2026-09-16

## Context

Every Python distribution in the monorepo shares one import root so a
namespace reads as `<root>.om.<ns>`, `<root>.infra.<capability>`,
`<root>.services.<svc>`. The guideline warns that a root named
`platform` shadows the standard-library module of the same name.

## Decision

The root package is `acme`, the product name. It is a namespace
package: no `acme/__init__.py` exists in any distribution, so
`acme-om`, `acme-infra`, and every service and worker contribute
subpackages to the same root.

## Consequences

Imports read `from acme.om.base import Platform`. Every new
distribution uses the `src/acme/...` layout and must not add a
`acme/__init__.py`.
