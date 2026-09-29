# ADR 0042: An owner deletes a team org, closed at once, and its providers by the queue

**Status**: accepted (2026-09-26). Builds on ADR 0041; deviates from
nothing.

## Context

A person deletes their account only once they are no longer the last
owner of a team org (ADR 0041). They had one way out in the API, making
someone else an owner, and none in the portal. An owner alone in a team
org had no way out at all: only an operator deletes a team org
(`DELETE /v1/admin/orgs/{org_id}`).

An operator's deletion soft-deletes the org and announces it. Every
principal of the tenant stops resolving, every socket closes, queued
work fails on its next claim, and the sweep purges every row of it
after the thirty-day retention. It leaves the providers alone: the
Stripe subscription keeps billing, the Slack app stays in the
workspace, and the WorkOS organization stays with its connections and
its pending invitations.

An owner who deletes their org means all of it. The people in it lose
it now. The plan stops billing. The Slack app leaves. A sign-in through
the org's single sign-on, or through an invitation to it, reaches
nothing.

## Decision

**An owner deletes their team org, from a session.** `POST
/v1/orgs/current/deletion` carries the org's name as the owner typed
it, forgiving surrounding space and nothing else. Only an owner's
session is heard: an admin, a member, and an api key are refused (`403
not_authorized`). A personal org is refused (`409 personal_org_fixed`):
it goes only with its person's account.

**The org closes in one commit.** One named atomic write,
`TenancyStorageInterface.write_closed_org`, lands the org row with its
WorkOS organization let go of, soft-deletes every live user of the
tenant with their membership, revokes every live session and api key,
and revokes every pending invitation. Each ended user lands
`tenancy.user.deleted`, and each credential its revocation row, so
every socket closes as a membership's end closes it. From the answer
on, nobody reaches the org: no credential works, no org picker offers
it, and no sign-in through WorkOS finds it, since the row no longer
names the WorkOS organization.

**The providers go through the queue, after the close.** The same
commit asks for `DELETE_ORG` in the org, with the WorkOS organization's
id in its payload. The worker deletes the WorkOS organization, cancels
the subscription and deletes the customer at Stripe, removes the Slack
app, and deletes the org, in that order. This is `DELETE_ACCOUNT`'s
order and its handler's pieces: each step finds its own work done on a
rerun; a provider out of reach, or a refused key, parks the item
without spending an attempt; a provider that refuses the call fails it
at once, for an operator to requeue.
Slack's side stays best effort.

The org waits, live and empty, for the providers, for ADR 0041's
reason: a claim refuses an item of a deleted org. The close is the
promise, and it is kept at once.

**The org's data goes the operator's way.** The worker's last step
writes `deleted_at`, keeps the org row as the record under its own
name, and announces `tenancy.org.deleted`. The sweep purges the tenant after the thirty-day retention.
There is no second deletion mechanism. A team org is not a person, so the retention that protects
against a mistake is kept; a personal org is still purged at once
(ADR 0041), since its person asked to be gone.

**An operator's deletion takes the same path.** `DELETE
/v1/admin/orgs/{org_id}` writes the same close and asks for the same
`DELETE_ORG`, so an org deleted on the operator plane stops billing,
leaves its Slack workspace, and lets go of its WorkOS organization. The
operator's identity is the actor of every row, and the worker deletes
the org under that name. The route keeps its write permission and its
answer, the org: its `deleted_at` stays unset until the worker has
deleted it. A repeat before then answers the org as it stands and asks
for nothing more. A personal org is refused (`409
personal_org_fixed`): it goes only with its person's account.

**The owner lands in their personal org.** The same request makes a
session in the owner's personal org, as a switch makes one, carrying
the provider's session the asking one came from. The answer carries it,
and the portal takes it up and opens the task list there. When that
session cannot be made, the answer carries none and the owner signs in
again: the org is deleted either way.

**The refusal of an account's deletion names both ways out.** The last
owner of a team org is told to make someone else an owner, or to delete
the organization, in that org's Settings. When the org named is the one
the tab is in, the card links to its members and to its deletion.

**Settings changes a member's role.** The member table has a role
column. A member who manages members gets a small menu on each other
member's role, holding the roles the server's rules let them give: never
their own role, never a member above their own role, never a role above
it. So an owner can make someone else an owner, and an admin cannot. The
route and its rules are the ones that were there (`PATCH
/v1/memberships/{user_id}`).

## Consequences

An owner can leave Acme without an operator: hand the org on, or
delete it, then delete the account.

The org's rows stay for thirty days after the worker deletes it. An
operator can read them in that time; nobody can restore them.

While a provider is down, or refuses the key, the org waits, empty, and
its subscription keeps billing until the provider answers. A provider
that refuses the call itself fails the item at once, as with an
account, and an operator requeues it (`acme-ops work requeue`).

An operator's deletion ends the providers too. Until the worker has
run, the operator plane lists the org live and empty, and counts it
among the tenants. Nobody joins it meanwhile: an operator's add of a
member to it is refused as not found.

The worker deletes the WorkOS organization with the Acme App's own
key, the one that already makes the API's organization calls and
deletes a deleted account's user. Stripe's runtime key already has
Customers and Subscriptions at Write. No key gains a permission.
