# Lenses

A lens is one rule from `architecture.md`, restated as something a
reviewer can check against code. The review skills load one group of
lenses at a time and judge a change from that perspective only, which
keeps each review narrow enough to be thorough.

## Groups

| Group id    | File            | Covers                                                                                          |
|-------------|-----------------|-------------------------------------------------------------------------------------------------|
| `om`        | `om.md`         | The Domain as the Source of Truth, Naming Entities, Namespaces as Swimlanes: source of truth, mixins, immutability, identifiers, namespaces, pure rules |
| `contracts` | `contracts.md`  | Interfaces, Separation of Layers, The Business Layer, The Network Layer (Service Interfaces, Direction of Calls), Cross-Cutting Conventions (App Container): interfaces, injection, wiring |
| `context`   | `context.md`    | OpContext, Separation of Layers, The Business Layer, The Storage Layer, Infrastructure, The Network Layer, Worker Roles: OpContext, AdminContext, authorization, tenancy, provenance |
| `storage`   | `storage.md`    | The Storage Layer and Identifiers: storage principles, tables, translation, roles, migrations |
| `async`     | `async.md`      | Infrastructure, Worker Roles, The Network Layer (idempotency, orchestration): infra, queues, workers, park vs fail |
| `network`   | `network.md`    | The Network Layer and Apps (Push-First Apps): topology, gateway, public types, clients, realtime, push-first |
| `delivery`  | `delivery.md`   | Apps, Deployment, Monorepo Folder Structure, Client App Architecture, Cross-Cutting Conventions, Technology Choices: apps, deployment, repo layout, client architecture, conventions, substitutions |

A rule belongs to exactly one group. Where two groups touch the same
section, the table in each file's header says which side of the line it
takes.

## Lens format

Every lens uses the same shape, so a skill can read any group the same
way:

```markdown
## OM-01 Title of the lens

**Principle.** The rule, in one or two sentences, in the guideline's voice.

**Source.** Naming Entities, Immutability.

**Look for.** What to inspect: files, signatures, declarations, call sites.

**Violation.** What evidence of a breach looks like, concretely.

**Severity.** high | medium | low
```

Ids are the group prefix plus a two-digit number: `OM`, `CON`, `CTX`,
`STO`, `ASY`, `NET`, `DEL`. Severity is the default weight of a breach:
`high` breaks a boundary or a guarantee, `medium` bends a shape the
guideline relies on, `low` is a convention.

`Look for` and `Violation` are prose, one to three sentences each, wrapped
at about 72 columns like the rest of the file.

`Source` names the section and, after a comma, the subsection, both by
title exactly as `architecture.md` spells them, never by number:
sections are inserted and removed, and a number would move under a
lens. Several citations are separated by `;`; a bare subsection after
a `;` belongs to the section cited before it.

A lens restates the guideline; it never adds a rule the guideline does
not state. When the guideline changes, the lens changes with it, and
`make check` confirms every lens still cites a section that exists.
