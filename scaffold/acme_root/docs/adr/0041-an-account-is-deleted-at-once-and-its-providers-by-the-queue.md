# ADR 0041: An account is deleted at once, and its providers by the queue

**Status**: accepted (2026-09-28)
## Context

A person asks to delete their account. What that means is decided:

- It is a hard delete. The person is gone now, and gone from the
  backups within seven days, the database's backup retention. There is
  no grace period and no undo. Signing up again with the same address
  makes a new person.
- It is refused while the person is the last owner of a team org, and
  refused for an operator, who leaves the allowlist first.
- What they made in a team org stays the org's, under their id only.
  Their user and their membership there go.
- Their personal org goes whole.
- Their user at the identity provider goes too, or a sign-in there
  would carry on as if nothing happened.

STO-32 (The Storage Layer, Database Roles) says otherwise: "A
soft-deleted row is purged by the maintenance sweep after its entity's
retention period, and the purge is the one hard delete. Personal data
lives in named fields, so erasing a person is a sweep over a list, not
a hunt." A soft delete keeps a user row's email and name for the
retention, which is the one thing the person asked not to happen.

## Decision

**The account goes in one commit, as a hard delete.** `POST
/v1/me/deletion`, from a session only, carries the account's email as
the person typed it. The tenancy manager checks it and the two
refusals: `409 last_owner`, whose envelope names each org by id, name,
and slug, and `403 operator_role_held`. Then one named atomic write,
`TenancyStorageInterface.delete_person`, deletes the identity with its
second factor, every user it is in any org with their memberships,
sessions, API keys, and socket tickets, its sign-ins and operator
tokens, the sign-in delay of its address, and every invitation sent to
that address or accepted by one of its users. It is a cross-tenant
method on the system scope, enumerated with the others. It counts,
under a row lock, the owners of each team org the person owns, and
refuses when one would be left with none, so two owners who leave at
once cannot both go.

This is the deviation from STO-32: a hard delete outside the sweep. The
personal fields are the named ones the guideline asks for, so the
delete is still a list, not a hunt.

**Every live credential is announced as revoked.** The same commit
lands `tenancy.session.revoked` and `tenancy.api_key.deleted` for each
live one, and `tenancy.user.deleted` in each org the person leaves, so
every socket they hold closes. Every payload carries ids only.

**A team org keeps the footprint, by id.** Nothing else the person made
there is touched. A client names an id it cannot resolve as a former
member.

**The provider goes through the queue, after the local delete.** The
same commit asks for `DELETE_ACCOUNT` in the personal org, with the
person's user id at the identity provider in its payload, since the
identity that held it is gone. The worker deletes that user, then the
personal org. A user the provider does not hold is deleted already,
so a rerun finds its work done. A provider out of reach, or one that
refuses the process's own key, parks the item for a minute without
spending an attempt. A provider that refuses the call itself fails the
item at once
([ADR 0051](0051-a-refused-key-is-unavailable-a-refused-request-is-refused.md)).

**The personal org goes through the deletion that exists.** The last
step soft-deletes the org and announces it. `tenancy.rules.past_retention`
counts a deleted personal org as past its retention at once, since it
is deleted only with its person. So the sweep's next pass runs the
tenant purge every namespace already has. There is no second deletion
mechanism. The org row stays as the record, as every deleted org's
does.

**The browser ends the provider's session too.** The answer carries
`provider_logout_url`, as a sign-out's does, and the portal goes there
on its way to `/signed-out`.

## Consequences

The local delete is the promise, and it is kept at once: from the
answer on, nobody signs in as the person and no credential of theirs
works. The org waits for the provider, because a claim refuses an item
of a deleted org.

While the provider is down, or refuses the key, the personal org's rows
wait, out of reach of their person. A provider that refuses the call
fails the item for good. Once the cause is fixed, a person with a
`write` operator entry sends the item back (`acme-ops work requeue`),
and the org's stream names who did.

A person who signs in through the provider before the item deletes
their user there is a new person in Acme. The item may then delete the
provider's user that new person signed in as. Their next sign-in makes
one again, and Acme links it by the verified address.

The outbox rows and work items of the deletion stay for their own
retention. They carry ids and the provider's user id, never a name or
an address.
