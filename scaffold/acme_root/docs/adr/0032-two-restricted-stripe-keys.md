# ADR 0032: Two restricted Stripe keys, one for the processes and one for the bootstrap

**Status**: accepted (2026-09-23). Supersedes the key's name and kind in
[ADR 0031](0031-plans-are-levers-and-the-processor-is-mirrored.md); the
rest of that record stands. The rollback's room is closed (2026-09-25):
`<prefix>stripe_org_key` is gone from every environment, and so is the
boot's refusal of `ACME_STRIPE_ORG_KEY`.

## Context

Two callers use Stripe, and they need different things.

- The API and the worker, all day: a customer per org (create, read), a
  hosted checkout, a portal session and the list of portal
  configurations, a subscription read and changed, and a price found by
  lookup key.
- `acme-ops stripe-bootstrap`, run by a person now and then: the
  products, the prices, the webhook endpoints, and the portal
  configuration, each listed, created, and updated.

One key for both holds every permission of both sets. The running
processes could then rewrite the catalog and register a webhook
endpoint, which only the bootstrap does. A key with that many
permissions is also easy to get wrong: Stripe's editor lists **Checkout
Sessions** as a group of its own, and setting Core or Billing to Write
does not include it.

The name `ACME_STRIPE_ORG_KEY` says "organization key". An
organization key reaches every account of the organization. What the
secret should hold is a restricted key of one account.

Stripe's documentation says the same three things: use restricted keys
instead of secret keys, give each key the least it needs, and use one
key per service or use case.

## Decision

**Two keys, each a restricted key of the environment's account.**

- The **runtime key** lives in the environment's secret store as
  `<prefix>stripe_runtime_key` and reaches the API and the worker as
  `ACME_STRIPE_RUNTIME_KEY`. It may write Customers, Checkout Sessions,
  Customer Portal, and Subscriptions, and read Prices.
- The **bootstrap key** lives in the shell of the person who runs the
  bootstrap, as `ACME_STRIPE_BOOTSTRAP_KEY`. It is never written to the
  cloud. It may write Products, Prices, Webhook Endpoints, and Customer
  Portal.

`acme.integrations.payments.permissions` lists both sets as the
editor names them. The runbook carries the same two tables.

**Only a restricted key is taken.** A process refuses at boot, and the
bootstrap refuses before its first call, a key that is an organization
key (it reaches every account) or a secret key (it may do everything in
the account), and a key whose mode is not the environment's.

**The processes check the runtime key at start.** They read one item
under each of its five permissions. A resource the key cannot read is
named in the start line and in an error log line that names the
editor's group. A key refused on every read is named as revoked or of
another account. A processor that does not answer leaves the check
open, and the process starts either way: billing is one part of it.

**The rename is a clean cut for the processes, with one release of
room for a rollback.** The processes read the new name only. A
process that finds `ACME_STRIPE_ORG_KEY` set refuses to boot and
names the two new variables, so a leftover line in a laptop's `.env`
is not a silent "not configured". The old secret container stays for
one release, and the serving tasks' execution roles keep reading it,
because the release before injects it and a rollback starts that
release. The release after this one removes it.

Reading the old name as a fallback was the other way. It is not taken:
the old secret holds the one key with both sets of permissions, and a
fallback would keep that key serving until someone noticed.

## Consequences

After the merge, staging's billing is unconfigured until a person makes
the runtime key and writes it into `acme/staging/stripe_runtime_key`.
The deploy makes that secret holding `off`. Every org keeps its plan in
between, and a checkout answers `503`.

A person who runs the bootstrap makes and keeps a second key. Rotating
it touches no environment. Rotating the runtime key touches no
bootstrap.

A new call to Stripe in the API or the worker needs its permission on
the runtime key, and in `RUNTIME_PERMISSIONS` if the boot check should
read it. A new call in the bootstrap needs its permission on the
bootstrap key. The runbook's tables are the list a person follows.
