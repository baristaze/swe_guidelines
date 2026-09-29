# ADR 0005: Infra has its own exception root

**Status**: accepted (2026-09-18)

## Context

The guideline says every exception raised inside the platform is rooted
at `PlatformException`, and since v0.4.0 it also says the object model
imports infra interfaces and infra imports nothing from the object
model. Both cannot hold in one class hierarchy: `PlatformException`
lives in the object model, so an infra impl that raises it imports the
model.

## Decision

`acme.infra.exceptions.InfraException` is a second root, a mirror of
the platform root with the same `http_status`, `code`, and `message`
shape. Every infra leaf (`BackendFailed`, `BlobNotFound`,
`InvalidBucketKey`, `SecretNotFound`, `SecretsFileNotPrivate`,
`PayloadMismatch`) derives from it. A boundary registers one handler per
root and presents both in the same envelope.

## Consequences

Every boundary that translates `PlatformException` also translates
`InfraException`; `services/api/gateway/errors.py` does, and a new
service copies the pair. Code that catches "any platform error" catches
both roots or neither. The two roots never merge until the guideline
gives the roots a shared home below the object model.
