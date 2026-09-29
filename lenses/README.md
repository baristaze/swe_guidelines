# Lenses

A lens holds the checkable detail under one rule of `architecture.md`:
the exact field, call, or breach a reviewer looks for in code. It is
stricter than the story on purpose, and never contrary to it. It never
adds a rule the guideline does not state. The review skills load one
group of lenses at a time, which keeps each review narrow enough to be
thorough.

## Groups

| Group id    | File            | Covers                                                                                          |
|-------------|-----------------|-------------------------------------------------------------------------------------------------|
| `om`        | `om.md`         | The Domain as the Source of Truth, Naming Entities, Namespaces as Swimlanes: source of truth, mixins, immutability, identifiers, namespaces, pure rules |
| `contracts` | `contracts.md`  | Interfaces, Separation of Layers, The Business Layer, The Network Layer (Service Interfaces and Impls, Direction of Calls), Cross-Cutting Conventions (The App Container): interfaces, injection, wiring |
| `context`   | `context.md`    | TenantContext (Stages, Scopes, The Operator Context), Separation of Layers, The Business Layer, The Storage Layer, Infrastructure, The Network Layer, Worker Roles, Telemetry (Correlation Across a Handoff), Cross-Cutting Conventions (Tests): stages, scopes, OperatorContext, authorization, tenancy, provenance |
| `storage`   | `storage.md`    | The Storage Layer and Identifiers: storage principles, tables, translation, roles, migrations |
| `async`     | `async.md`      | Infrastructure, Worker Roles, The Network Layer (Idempotency on the Consumer Side, Long-Running Orchestrations), Telemetry (Correlation Across a Handoff): infra, queues, workers, park vs fail |
| `network`   | `network.md`    | The Network Layer, Apps (Push-First Apps), and Client App Architecture (Realtime: One Channel per App): topology, gateway, public types, clients, realtime, push-first |
| `delivery`  | `delivery.md`   | Apps, Deployment, Monorepo Folder Structure, Client App Architecture, Telemetry, Cross-Cutting Conventions, Technology Choices: apps, deployment, repo layout, client architecture, logs and telemetry, conventions, substitutions |
| `ops`       | `ops.md`        | Operations, Documentation as Code: roles, credentials, ops skills, alarms, scale-out, cost, environments, traffic, READMEs |

A rule belongs to one group, and to one lens in it. The `Covers` column
names each group's home sections. Where two groups touch one section,
the paragraph at the top of each file draws the line. A lens may cite a
section outside its group's home when its rule rests there. Where two
lenses meet one breach, the one a reviewer reaches first names the
other in parentheses, and the lens it names carries the severity.

## Lens format

```markdown
## OM-01 Title of the lens

**Principle.** The rule, in a few short sentences, in the guideline's voice.

**Source.** Naming Entities, Immutability.

**Look for.** What to inspect: files, signatures, declarations, call sites.

**Violation.** What evidence of a breach looks like, concretely.

**Severity.** high | medium | low

**Shape.** `scaffold/acme_root/om/src/acme/om/base.py`

**Check.** `arch-check` decides it.
```

Ids are the group prefix and two digits: `OM`, `CON`, `CTX`, `STO`,
`ASY`, `NET`, `DEL`, `OPS`.

- **Principle** is at most 120 words. A rule that needs more is two
  lenses.
- **Source** names the section and, after a comma, the subsection, by
  title as `architecture.md` spells them, never by number. Several
  citations are separated by `;`.
- **Look for** and **Violation** are one to three sentences each,
  concrete enough that two reviewers flag the same line.
- **Severity** is `high` for tenancy, authorization, a secret that
  reaches where it is not held, lost or duplicated work, and a
  cross-role breach. `medium` bends a shape the guideline relies on, and
  `low` is a convention. A lens that cites only `style` sections is
  `low`.
- **Shape** is optional. It names one or two files or folders of the
  scaffold, `scaffold/acme_root/<path>` in backticks, that show the rule
  as code. A review reads the file and compares the code with it. A lens
  has one only where a scaffold file shows its rule plainly.
- **Check** is optional. "`arch-check` decides it." means the checker
  decides the whole lens. "`arch-check` decides `<the part>`; the rest is
  judged." means the review judges what the checker leaves. A lens with
  no Check line is judged by the review alone.

`make lenses` holds the format, a width of 80 columns, the citations,
each Shape path, the Check line against the checker's rules, and every
identifier a lens quotes to the section it cites. That a lens stays
inside its rule, stricter and never contrary, is held by review, not by
a program.
