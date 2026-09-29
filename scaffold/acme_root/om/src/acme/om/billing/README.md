# Billing

What an org's plan is, what it lets the org have, and how the org pays
for it. This is one of the seven kinds of thing
[Acme is made of](../../../../README.md).

## The plans

Every org is on one plan. A plan belongs to the org, never to a person:
the owner pays for the team, and a person who is a member of someone
else's paid org gets nothing from it in their own. Every org starts on
Free, a person's own org included, and any org can move to any plan.

| Plan | Price a month | Members | API keys | Active tasks | Files |
|------|---------------|---------|----------|--------------|-------|
| Free | nothing | 1 | none | 10 | 1 GB |
| Pro | $5 | 1 | yes | no bound | 10 GB |
| Team | $10 | up to 5 | yes | no bound | 50 GB |
| Max | $30 for up to 10 members, then $3 a member for all of them | no bound | yes | no bound | 200 GB |

The numbers are illustrative; the shape is what holds. So eleven members
on Max are $33 a month, and ten are $30.

An **active task** is a task that is neither done nor deleted. A
**member** is a person with a live membership in the org, the owner
included.

## What a bound does

A bound is a lever, never a hidden feature. Every org sees every feature.
When a change would take the org past a bound of its plan, the change is
refused with a refusal that names what was bound, the plan, the bound,
and the first plan that lifts it; a screen turns that into an offer to
upgrade. Only an owner or an admin can change the plan; a member is told
to ask one.

- The eleventh active task on Free is refused. Reopening a done task
  counts as one more active task.
- A member past the plan's seats is refused.
- The first API key on Free is refused, and so is every one after it.
- An import of tasks that reaches the bound is not refused: it parks,
  keeping the tasks it made, and goes on when the plan rises. A change
  of plan that lifts the org's bounds (a payment, a grant) wakes it, in
  the same commit that records the new plan.

Nothing the org already has is ever taken away by a bound. An org that
moves to a smaller plan keeps its tasks, its members, and its keys, and is
refused only what would add to them until it is back under the bound. Its
API keys are kept too, and are refused while the org is on a plan without
them: each answers with the same refusal, and works again the day the org
is on a plan with keys. A key is never revoked for a plan.

The files bound is part of every plan. The billing page shows what the
org keeps beside it, counted by the media namespace. Nothing is refused
on it yet: an upload past the bound still lands.

## Paying

The payment processor owns money, customers, and subscriptions. Acme
owns what a plan entitles an org to. The org's **billing account** is
Acme's copy of what the processor holds: the org's customer there, its
subscription, the subscription's price, status, and the end of the period
it has paid for, whether it is set to end then, and how many seats it
pays for. Acme writes that copy only from what the processor answers
when asked, never from anything a person sends.

- **Upgrading** starts a checkout on the processor's own page. The org's
  customer is made the first time. Once the person has paid, the
  processor tells Acme, and the org is on its new plan.
- **Changing between paid plans**, the payment method, and the invoices
  are on the processor's own page, which an owner or an admin opens from
  the billing page.
- **Cancelling** sets the plan to end when the period the org paid for
  ends. The org keeps the plan until then, and is on Free after it, or
  on the plan an operator granted. Cancelling can be taken back before
  the end.
- **A payment that failed** leaves the org on its plan while the
  processor tries again. When the processor gives up, the org is on Free.
  Either way, every owner and admin sees it: a notice on the billing
  page and a banner on every other page, with a button to the
  processor's page for a new payment method. The notice stays until a
  later payment goes through or the subscription ends. A member sees
  nothing, since a member cannot fix it.
- On **Max** the subscription pays for the org's active members. When a
  member is added or removed, the count is brought in step in the
  background; the change is billed from the next invoice, with no charge
  or credit in between.

## What the processor tells Acme

The processor tells Acme about a change by sending a **delivery** to
Acme's address. A delivery is believed only when the processor's
signature over it checks out. What it says is never trusted as the state:
deliveries arrive late, twice, and out of order, so Acme reads the
subscription from the processor again and copies what it holds now. Each
delivery is applied once. Its **mark** is written with the account it
changed, and a second copy of it changes nothing. A delivery that names
an org whose account belongs to another customer changes nothing either.

Marks are kept for thirty days, past any retry the processor makes.

## Reading the plan

Every lever asks what plan the org is on: a new task, a member, a key.
The billing page asks it too, beside the org's usage. So the account is
cached, per org, for a minute at most.

- Every change to the account forgets the cached copy at once: a
  delivery, a checkout that makes the customer, a cancellation and its
  undo, a seat count, a grant, and an account closed at the processor.
  The next read asks the database.
- When the cache cannot be told of a change, the old copy answers until
  its minute is up, never longer. `ACME_BILLING_ACCOUNT_CACHE_SECONDS`
  sets that minute.
- When the cache is down, every read asks the database. Nothing is
  refused for it.
- Who may see the plan is decided on every call, before the cache.
- The usage beside the plan (members, files, active tasks) is counted
  fresh every time. A bound is enforced on an exact count.
- A checkout, the processor's portal, and every write read the account
  from the database, never from the cache.

## Grants

An operator can put an org on a plan with no payment: support, a
partner, the tenants of a load test. A laptop's seed grants its team org
Team the same way. The org is on the higher of the
plan it pays for and the plan it was granted. A grant can be taken back.
