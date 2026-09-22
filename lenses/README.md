# Lenses

A lens is one rule from `architecture.md`, restated as something a
reviewer can check against code. The review skills load one group of
lenses at a time and judge a change from that perspective only, which
keeps each review narrow enough to be thorough.

## Groups

| Group id    | File            | Covers                                                                                          |
|-------------|-----------------|-------------------------------------------------------------------------------------------------|
| `om`        | `om.md`         | The Domain as the Source of Truth, Naming Entities, Namespaces as Swimlanes: source of truth, mixins, immutability, identifiers, namespaces, pure rules |
| `contracts` | `contracts.md`  | Interfaces, Separation of Layers, The Business Layer, The Network Layer (Service Interfaces and Impls, Direction of Calls), Cross-Cutting Conventions (The App Container): interfaces, injection, wiring |
| `context`   | `context.md`    | OpContext (Stages, Scopes, The Operator Context), Separation of Layers, The Business Layer, The Storage Layer, Infrastructure, The Network Layer, Worker Roles, Telemetry (Correlation Across a Handoff), Cross-Cutting Conventions (Tests): stages, scopes, OperatorContext, authorization, tenancy, provenance |
| `storage`   | `storage.md`    | The Storage Layer and Identifiers: storage principles, tables, translation, roles, migrations |
| `async`     | `async.md`      | Infrastructure, Worker Roles, The Network Layer (Idempotency on the Consumer Side, Long-Running Orchestrations), Telemetry (Correlation Across a Handoff): infra, queues, workers, park vs fail |
| `network`   | `network.md`    | The Network Layer, Apps (Push-First Apps), and Client App Architecture (Realtime: One Channel per App): topology, gateway, public types, clients, realtime, push-first |
| `delivery`  | `delivery.md`   | Apps, Deployment, Monorepo Folder Structure, Client App Architecture, Telemetry, Cross-Cutting Conventions, Technology Choices: apps, deployment, repo layout, client architecture, logs and telemetry, conventions, substitutions |
| `ops`       | `ops.md`        | Operations, Documentation as Code: roles, credentials, ops skills, alarms, scale-out, cost, environments, traffic, READMEs |

A rule belongs to exactly one group, and to exactly one lens inside it.
The `Covers` column names each group's home sections; where two groups
touch the same section, the paragraph at the top of each file says what
it leaves to its neighbours, and that paragraph is the line. A lens may
cite a section outside its group's home sections when its rule rests
there, so `Source` is where the rule is written down and the group is
who judges it. Where two lenses sit next to one breach, the one a
reviewer reaches first names the other in parentheses (`(Ids read back
out of the database are STO-06.)`) rather than both flagging it, and
the lens that is named carries the severity.

## Lens format

Every lens uses the same shape, so a skill can read any group the same
way:

```markdown
## OM-01 Title of the lens

**Principle.** The rule, in a few short sentences, in the guideline's voice.

**Source.** Naming Entities, Immutability.

**Look for.** What to inspect: files, signatures, declarations, call sites.

**Violation.** What evidence of a breach looks like, concretely.

**Severity.** high | medium | low

**Check.** `arch-check` decides it.
```

Ids are the group prefix plus a two-digit number: `OM`, `CON`, `CTX`,
`STO`, `ASY`, `NET`, `DEL`, `OPS`. Severity is the default weight of a
breach: `high` is reserved for tenancy, authorization, a credential or
a secret reaching somewhere it is not held, lost or duplicated work,
and a cross-role breach, about a quarter of the catalog; `medium`
bends a shape the guideline relies on; `low` is a convention.

`Principle` is at most 120 words: a rule that needs more is two lenses.
`Look for` and `Violation` are prose, one to three sentences each, wrapped
at about 72 columns like the rest of the file, and no line is wider than
80 columns. `make lenses` holds every lens to the word count and the
column limit, and `Look for` and `Violation` to three sentences; the
wrap is read by a person.

`Source` names the section and, after a comma, the subsection when the
rule rests in one, both by title exactly as `architecture.md` spells
them, never by number: sections are inserted and removed, and a number
would move under a lens. A rule stated in a section's own introduction
cites the section alone. Several citations are separated by `;`; a
bare subsection after a `;` belongs to the section cited before it.

`Check` is optional. It says that `arch-check`, the static checker in
`checkers/`, decides the lens, in one of two sentences. "`arch-check`
decides it." means the checker decides the whole lens, and the review
skills take its result without judging the code again. "`arch-check`
decides `<the part>`; the rest is judged." means the checker decides a
named mechanical part, and the review judges what remains. A lens with
no `Check` line is judged by the review alone. The checker's rule id is
the lens id, and the two are held together both ways: every lens with
a `Check` line has a rule of that id with the same coverage (`make
lenses`) and severity (`make test`), and every rule has a lens that
says so. A rule ships only
when it is deterministic and rarely wrong on a tree shaped the way the
guideline prescribes; a rule that would guess stays with the review.

A lens restates the guideline; it never adds a rule the guideline does
not state. When the guideline changes, the lens changes with it, and
`make check` confirms every lens still cites a section that exists.
