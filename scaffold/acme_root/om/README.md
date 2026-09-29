# What Acme is made of

Acme is a to-do list for a team. This page names the things the
product is made of and says how they relate. It is written for anyone;
you need no code to read it. Each kind of thing has a page of its own
one level down, and that page says what can happen to it and which
rules always hold.

## The team and the people in it

An **organization**, org for short, is one team: a company, a club, a
family. Everything in Acme belongs to exactly one org, and nothing in
one org can see anything in another. That is a rule, not a choice.

Every person has one **personal org**, their own place to work. It is
made with them the first time they sign in, named after them, and it is theirs
for as long as they exist: it goes only when they delete their account,
and it never changes hands. Every other org is a **team org**, which a person makes when a
team needs one and owns from then on. A person can belong to many team
orgs, and anyone can be added to a personal org too.

An **identity** is one person, across every org. It is the email the
person signs in with, which the sign-in provider has verified. One
person has one identity, however many teams they belong to. Acme keeps
no password: WorkOS, the sign-in provider, proves who the person is,
with an email code or link, Google, GitHub, or their company's single
sign-on.

A **user** is that person inside one org: the name the team sees. The
same identity is a different user in each org it belongs to, and a
user in one org knows nothing of the same person in another.

A **membership** is the user's place in the org. It carries a role:
viewer, member, admin, or owner. The role decides what the person may
do in that org. The person who creates the org is its first owner, and
the person of a personal org is its owner for good.

An **invitation** asks a person to join an org, by email, with a role.
The sign-in provider sends the email; signing in through its link makes
the person a member.

## How a person proves who they are

A **session** is a signed-in visit. Signing in through the sign-in
provider gives one. It is good for a while, and it ends when the
person signs out or when it expires. Signing out in the browser also
ends the sign-in provider's own session there, so the next person at
that computer is asked who they are. A session belongs to one user in
one org, so a person who belongs to two teams picks which team they
are visiting.

An **API key** is a long-lived credential for a program: a script, an
agent, a tool on a laptop. A member makes it, names it, and gives it a
role that is never above their own. It expires, and it can be revoked
at any time. Acme keeps only a fingerprint of the key, never the key
itself, so the key is shown once, when it is made.

A **socket ticket** is a small pass that opens the live channel, the
connection through which changes reach a screen the moment they
happen. It works once and it expires in minutes. It stands for the
session or the API key that asked for it, so it opens nothing they
could not open themselves.

## The plan

A **plan** is what an org is entitled to: how many members, whether it
may have API keys, how many active tasks, and how much room for files.
Every org is on one, Free to begin with. The plan belongs to the org,
never to a person: the owner pays for the team.

A **billing account** is the org's place at the payment processor: its
customer there, and Acme's copy of the subscription the org pays for.
The processor owns the money; Acme owns what a plan lets the org have,
and copies what the processor holds whenever it says something changed.
An operator can also grant an org a plan with no payment.

A **delivery mark** says that one message from the processor has been
applied, so the same message arriving again changes nothing.

## The work

A **task** is one item on the list: a title, notes, who it is assigned
to, and whether it is open or done. Open tasks sit in the order the
team arranged them; done tasks are listed newest first. A task
remembers who created it and who last changed it. It can carry a
**due date**, a day and never an hour. On the morning of that day, at
nine where the person it is for lives, the team gets one reminder, on
every open screen and in the org's Slack channel.

Tasks can be **imported** from a CSV file: one row, one task. A done
task left unchanged for ninety days is **archived** by a cleanup that
runs once a day: it leaves the done list and every count, and it can be
read and restored.

An **orchestration** is one long job, such as an import or a day's
cleanup, kept as a record with a status and a place where the next step
starts. A worker does it a batch at a time, and a job that must wait
(an import that reached the plan's bound of active tasks) parks with
what it made kept, until the reason is gone.

## Slack

A **Slack installation** is the Acme app added to one Slack workspace
for an org. An owner or an admin installs it from Settings with "Add to
Slack". The org has at most one, and a workspace belongs to one org.
The app's key into that workspace, its bot token, is the org's own
secret, kept in the secret store and never on the record.

The installation's **channel** is where reminders, new tasks, and
finished ones appear. An owner or an admin picks it by typing
`/acme connect` there. In any channel the app is in, `/acme` shows
your open tasks, `/acme team` the org's, and `/acme add <title>` adds
a task. Acme knows who typed by the email address Slack holds for
them, which must be one a member signed in with.

A **file** is something a person keeps with the work: a document, a
picture, a recording. Acme keeps a record of the file (its name, its
type, its size, who uploaded it, and why it is there) and keeps the
bytes themselves in a separate store, never in the record. A file on a
task is an **attachment**. The org's files add up to its storage used.

## What the platform writes for itself

The things above are what people see and touch. The four below are how
Acme keeps its promises. No person creates them and no screen shows
them, but every one of them belongs to an org like everything else.

An **event** is a line in the org's diary: which thing changed, how,
by whom, and when. The lines are numbered one, two, three, with no
gaps, and a line is never edited. The oldest lines go after a
retention, and the diary starts after the last one gone; the whole
diary goes with its org, once a deleted org's retention has passed. A line about a person records that
something happened to them, never their email or name. The live channel pushes
each new line to every open screen. A screen that was away reads the
diary from the last number it saw and catches up. A screen away for
longer than the diary reaches back reads everything afresh instead.

An **outbox row** is a note Acme writes beside a change, in the same
stroke as the change itself, saying "tell everyone about this". From
the note comes the event, and from the event the push. Because the
note and the change are written together, no change is ever made
without its announcement, and no announcement is made of a change that
did not happen.

A **work item** is a job for later: something the platform does in the
background on behalf of a person who asked once, such as the reminder a
due date scheduled or a post to Slack. It waits in a queue, some of it
until a set time.
A worker claims it, holds it for a short lease, does it, and marks it
done. If the worker dies, the lease runs out and another worker picks
the job up.

An **idempotency record** remembers the outcome of a request that
might arrive twice, such as a "create task" that a flaky network
repeats. The second copy gets the first copy's answer instead of making
a second task.

## How they fit together

- An org has users. A user is one identity's place in that org, held
  by a membership with a role.
- An identity has exactly one personal org, and a user and an owner
  membership in it.
- A session or an API key belongs to a user, so everything done with
  it is done as that user in that org. A socket ticket stands for one
  of them.
- Tasks belong to the org. Each is created by a user and may be
  assigned to a user. A task added from Slack is created by the member
  who typed it; a task imported from a file, by the member who started
  the import.
- An orchestration belongs to the org. An import is started by a user
  and reads a file of the org; the day's cleanup is the platform's.
- A Slack installation belongs to the org, and a workspace to one org.
- An org is on one plan. The plan bounds its members, its API keys,
  and its active tasks; meeting a bound is a refusal that offers the
  plan that lifts it, and nothing is ever taken away. The billing
  account and its delivery marks belong to the org like everything
  else.
- Files belong to the org. Each is uploaded by a user, for a purpose;
  an attachment names the task it is on, and goes when the task goes.
- Every change to a task or a file, and every change to a user, a membership, a
  session, or an API key that the org's screens act on (a member added,
  changed, or removed, a credential revoked), writes an outbox row, which
  becomes an event in the org's diary, which reaches every screen of the
  org. Signing in, and creating an org, announce nothing.
- A work item, an idempotency record, an event, and an outbox row each
  name the org they belong to, so the fence between orgs holds for them
  too.

## One page per kind

- [Orgs, identities, users, memberships, sessions, API keys, and socket tickets](src/acme/om/tenancy/README.md)
- [Tasks](src/acme/om/tasks/README.md)
- [Orchestrations: imports and cleanups](src/acme/om/orchestrations/README.md)
- [Files](src/acme/om/media/README.md)
- [Slack installations and posts](src/acme/om/slack/README.md)
- [Plans, billing accounts, and delivery marks](src/acme/om/billing/README.md)
- [Events](src/acme/om/events/README.md)
- [Work items](src/acme/om/work/README.md)
- [Idempotency records](src/acme/om/idempotency/README.md)
- [Outbox rows](src/acme/om/outbox/README.md)
