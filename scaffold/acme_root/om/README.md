# What Acme is made of

This page names the things Acme is made of and says how they relate.
It is written for anyone; you need no code to read it. Each kind of
thing has a page of its own one level down, which says what can happen
to it and which rules always hold. A product built on Acme adds its own
kinds beside these.

## The org and the people in it

An **organization**, org for short, is one tenant: a company, a team, a
household. Everything in Acme belongs to exactly one org, and nothing
in one org can see anything in another. That is a rule, not a choice.

Every person has one **personal org**, made with them the first time
they sign in. It is theirs for as long as they exist, and it goes only
with their account. Every other org is a **team org**, which a person
makes and owns.

An **identity** is one person across every org: the email the sign-in
provider has verified. Acme keeps no password.

A **user** is that person inside one org: the name the org sees.

A **membership** is the user's place in the org, with a role: viewer,
member, admin, or owner. The role decides what the person may do there.

An **invitation** asks a person to join an org, by email, with a role.
The sign-in provider sends the email.

## How a person proves who they are

A **session** is a signed-in visit to one org. It ends when the person
signs out, when it sits idle too long, or when it reaches its lifetime.

An **API key** is a named, expiring credential for a program, with a
role no higher than its maker's. Acme keeps only its fingerprint, so
the key is shown once.

A **socket ticket** is a one-use pass that opens the live channel, the
connection through which changes reach a screen as they happen.

An **operator** is a person on the platform's own allowlist. An
operator works across orgs with an **operator token**: one permission,
an hour at most.

## Files

A **file** is a record of something an org keeps in the object store:
its name, type, size, who uploaded it, and why. The bytes live in the
store, never in the record.

## What the platform writes for itself

No person creates these and no screen shows them, but each belongs to
an org like everything else.

An **event** is a line in the org's diary: what changed, how, by whom,
and when. The lines are numbered with no gaps. An **audit entry** is an
event the platform records about itself, such as a job that failed for
good.

An **outbox row** is a note written in the same stroke as a change,
saying "tell everyone about this". The event and the live push come
from it, so no change goes unannounced.

A **work item** is a job for later. It waits in a queue until a worker
claims it, holds it for a short lease, and does it.

An **orchestration** is a long job kept as a record, done one step at a
time. It succeeds, fails, or parks until what it waits for is back.

A **lease** lends one holder a thing only one may use at a time, such
as a loading dock, for a term it renews. Each grant carries a token
higher than the last, and the others wait in line.

An **idempotency record** remembers the outcome of a request that may
arrive twice, so the second copy gets the first one's answer.

## How they fit together

- An org has users. A user is one identity's place in the org, held by
  a membership with a role.
- A session or an API key belongs to a user, so everything done with
  it is done as that user in that org.
- Every change a screen acts on writes an outbox row, which becomes an
  event in the org's diary, which reaches every open screen of the org.
- A work item, an orchestration, an idempotency record, an event, and
  an outbox row each name their org, so the fence between orgs holds
  for them too.

## One page per kind

- [Orgs, identities, users, memberships, and credentials](src/acme/om/tenancy/README.md)
- [Files](src/acme/om/media/README.md)
- [Events](src/acme/om/events/README.md)
- [Outbox rows](src/acme/om/outbox/README.md)
- [Work items](src/acme/om/work/README.md)
- [Orchestrations](src/acme/om/orchestrations/README.md)
- [Resources, leases, and the line](src/acme/om/leases/README.md)
- [Idempotency records](src/acme/om/idempotency/README.md)
