# ADR 0042: An owner deletes a team org, closed at once, and its providers by the queue

**Status**: accepted (2026-09-28)
## Context

A person deletes their account only once they are not the last owner
of a team org
([ADR 0041](0041-an-account-is-deleted-at-once-and-its-providers-by-the-queue.md)).
So an owner needs two ways out: make someone else an owner, or delete
the org.

An owner who deletes their org means all of it. The people in it lose
it now. A sign-in through the org's single sign-on, or through an
invitation to it, reaches nothing. The org's organization at the
identity provider, with its connections and pending invitations, goes
too.

## Decision

**An owner deletes their team org, from a session.** `POST
/v1/orgs/current/deletion` carries the org's name as the owner typed
it, forgiving surrounding space and nothing else. Only an owner's
session is heard: an admin, a member, and an API key are refused (`403
not_authorized`). A personal org is refused (`409 personal_org_fixed`):
it goes only with its person's account.

**The org closes in one commit.** One named atomic write,
`TenancyStorageInterface.write_closed_org`, lands the org row with its
provider organization let go of, soft-deletes every live user of the
tenant with their membership, revokes every live session and API key,
and revokes every pending invitation. Each ended user lands
`tenancy.user.deleted`, and each credential its revocation row, so
every socket closes. From the answer on, nobody reaches the org: no
credential works, no org picker offers it, and no sign-in through the
provider finds it.

**The provider goes through the queue, after the close.** The same
commit asks for `DELETE_ORG` in the org, with the provider
organization's id in its payload. The worker deletes that organization,
then the org. Each step finds its own work done on a rerun. A provider
out of reach, or one that refuses the process's own key, parks the item
without spending an attempt. A provider that refuses the call fails it
at once, for an operator to requeue
([ADR 0051](0051-a-refused-key-is-unavailable-a-refused-request-is-refused.md)).
The org waits, live and empty, because a claim refuses an item of a
deleted org.

**The org's data goes the retention's way.** The worker's last step
writes `deleted_at`, keeps the org row as the record, and announces
`tenancy.org.deleted`. The sweep purges the tenant after the thirty-day
retention. A team org is not a person, so the retention that protects
against a mistake is kept.

**An operator's deletion takes the same path.** `DELETE
/v1/admin/orgs/{org_id}` writes the same close and asks for the same
`DELETE_ORG`, with the operator's identity as the actor of every row.
It answers the closed org, whose `deleted_at` stays unset until the
worker has deleted it. A repeat before then answers the org as it
stands and asks for nothing more. A personal org is refused.

**The owner lands in their personal org.** The same request makes a
session in the owner's personal org, as a switch does, carrying the
provider session the asking one came from. The portal takes it up.
When that session cannot be made, the answer carries none and the owner
signs in again: the org is deleted either way.

**Settings changes a member's role.** A member who manages members gets
a role control on each other member (`RoleControl`), holding the roles
the server's rules let them give: never their own role, never a member
above their own role, never a role above it. So an owner can make
someone else an owner, and an admin cannot. The route is `PATCH
/v1/memberships/{user_id}`.

## Consequences

An owner can leave without an operator: hand the org on, or delete it,
then delete the account.

The org's rows stay for thirty days after the worker deletes it. An
operator can read them in that time; nobody can restore them.

While the provider is down, or refuses the key, the org waits, empty.
Until the worker has run, the operator plane lists the org live and
empty, and counts it among the tenants. Nobody joins it meanwhile: an
operator's add of a member to it is refused as not found.

The worker deletes the provider organization with the Acme App's own
key, the one that makes the API's organization calls. No key gains a
permission.
