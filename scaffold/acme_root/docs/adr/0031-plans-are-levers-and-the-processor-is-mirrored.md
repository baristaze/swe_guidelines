# ADR 0031: Plans are levers, and the payment processor is mirrored

**Status**: accepted (2026-09-22). The key's name and kind are superseded by [ADR 0032](0032-two-restricted-stripe-keys.md).

## Context

Acme gains plans: Free, Pro, Team, and Max, each bounding an org's
members, its API keys, its active tasks, and its room for files, and each
paid for through Stripe. Two systems then hold a view of what an org may
do. Stripe owns money, customers, and subscriptions; Acme owns what a
plan entitles an org to. The guideline names the shapes this touches (a
provider behind an interface with a twin, an inbound webhook authenticated
by its signature and queued, a work item that follows a core write, the
process's own credentials), and leaves the product's choices to the
product. This record names them.

## Decision

**A plan belongs to an org.** The owner pays for the team. A person's own
org is an org like any other and starts on Free. The bounds and the prices
are one pure table in `billing.rules`, the one place a number lives; the
Stripe definition in `deployment/stripe/desired-state.json` is held to it
by a test at ten and eleven seats.

**The mirror is written from reads, never from payloads.** The billing
account copies the subscription (customer, subscription, price lookup
key, status, period end, cancel at period end, quantity), and every
delivery reads the subscription from Stripe again instead of trusting
the event's snapshot, so deliveries out of order converge on what Stripe
holds now. A subscription the account does not follow is copied only when
it is live, so a late event about an ended subscription never overwrites
the new one. The plan is derived from the mirror and the table: a live
status (active, trialing, past due) carries its plan, and a subscription
set to end carries it until the period's end, whether or not Stripe has
said it ended yet. An operator's grant is a second source, and the org is
on the higher of the two.

**A bound is a typed refusal.** Meeting one raises `PlanLimitReached`, a
402 with code `plan_limit_reached`, whose envelope carries the lever, the
plan, the bound, and the first plan that lifts it; the portal turns it
into an upgrade. The edge's idempotency marker is released on a 402, as
on a 429, because both are answers about now: the retry of the same
create lands once the org has room.

**Nothing is taken away.** After a downgrade the org keeps its tasks, its
members, and its keys, and is refused only what would add to them. An API
key of an org on a plan without keys is kept and refused at
`authenticate` with the same 402, rather than revoked or answered with a
401: the owner of the key reads why, nothing has to be made again, and the
key works the day the org is back on a plan with keys.

**The active-task bound is a lever, not a fence.** It reads the count and
then writes, so two creates that race at the bound can both land; the
next one is refused. A count under the row lock of the insert would hold
the line exactly at the cost of a statement every create pays, and a
plan's bound does not need that.

**Seeding is not bound by seats, and grants its own team a plan.** The
seed's `add_member` is the platform arranging a laptop, not a tenant
adding someone. `bootstrap --plan team` grants the seeded Ajax Team
through `grant_seeded_plan`, which takes only the internal, owner-held
context a seeding transition produces, so no route reaches it; the
operator plane's grant is the one a deployed environment has. Every
other door is bound by seats: an invitation is refused before anything
is sent to the identity provider, its acceptance asks again (a seat can
be taken between the two; without one the invitation stays pending and
the sign-in goes on), a join through the org's single sign-on takes a
seat, and so does the operator plane's add.

**The Max quantity follows the members through the queue.** A member added
or removed on Max lands a second outbox row, `work.SYNC_SEATS`, in the
change's commit, on the path reminders and Slack posts took first (ADR
0012). The handler reads the member count when it runs, so items that
run late or twice converge, and sets the quantity with
`proration_behavior=none` under a key made of the item and the count: a
seat is billed from the next invoice, with no prorated line per change
and nothing to credit when one is added and removed within a period.
`WORK_ENQUEUE_PERMISSIONS` names the permission each kind is asked for
with, and the worker's tests hold every role that holds it to the
permissions its handler's calls take.

**Deliveries are queued, then applied once.** `POST /webhooks/stripe` sits
outside `/v1`, since the shape is Stripe's and is pinned on the endpoint.
It checks the signature over the body and the timestamp with the SDK's
own check, the one `Webhook.construct_event` runs, and its five-minute
window, and queues the
delivery on `acme-webhooks` with a UUID v5 of the event id as its key;
one that fails is a 400 and nothing is queued. The route has no rate
limit of its own: there is no path token to key one on, the check is one
HMAC, and admission bounds the process. The maintenance worker consumes
the queue beside its claim loop, and the delivery's mark lands in the
account's commit, so a second copy changes nothing.

**The key and the signing secret are process credentials.** They are the
platform's, not a tenant's, so they are not read through
`SecretsInterface`, whose names carry a tenant; they reach the API and the
worker at start, like the error tracker's DSN, from
`<prefix>stripe_org_key` and `<prefix>stripe_webhook_secret`, which
Terraform creates as "off" and never writes again. "off" leaves billing
unconfigured: every org keeps its plan and a checkout answers 503. The
account id is committed per environment and sent as `Stripe-Context` on
every call, with a pinned `Stripe-Version`. A process refuses a key whose
mode is not its environment's: production a live key, everything else a
test key.

**A change between paid plans happens in Stripe's portal.** Acme starts a
checkout only for an org with no paid plan and refuses a second one
(`subscription_exists`); the portal configuration the bootstrap manages
allows the switch between the three prices, and cancel at the period's
end.

## Consequences

Every lever reads one account row per check, through
`EntitlementsInterface`, the one part of billing the tasks and tenancy
managers hold. The billing and tenancy managers each ask the other one
question (the entitlements, and whether a tenant is past its retention),
so that edge is bound at call time in the root, as the relay's is. The
storage bound is shown beside the org's usage and refuses nothing yet;
the media namespace is the one to ask it, when uploads are held to it. A deployed environment serves Free to
everyone until its key is set.
