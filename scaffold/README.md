# Scaffold

`acme_root/` is the domain-agnostic core of a system in the guideline's
shape. It is a whole monorepo that runs, named `acme`, with no product
domain in it. A new project starts as a copy of it and builds its domain on
top.

## What is in it

Everything a multi-tenant product needs before its first domain screen:

- **The object model** (`om/`): tenancy with the operator plane, events and
  audit, the outbox, idempotency, the work queue, orchestrations, and media.
  Storage runs on four database roles, each with its own migration chain
  and a row-level security fence.
- **Infra and integrations**: cache, buckets, topics, queues, secrets, and
  observability; the identity provider with its twin; a webhook ingress
  that checks a signature at the edge and hands the delivery to a worker.
- **Processes**: the API with its gateway and realtime socket, and the
  maintenance worker with its sweep.
- **Apps and clients**: the portal shell, the CLI, the company site, and
  the TypeScript and Python clients, over the horizontal API.
- **Operations**: the ops package and its skills, the local stack with its
  dashboards, Terraform for the cloud, Dockerfiles, and CI and deploy
  workflows.

## What is out

Any product domain. There is no placeholder entity: `media` shows the
plain shape of a namespace. A product adds its own namespaces, routes,
screens, and seed rows.

## Copy it

```bash
python3 scaffold/new.py ~/code/pressroom
cd ~/code/pressroom && make setup && make check
```

The last part of the path is the name: one or two snake_case words.
`new.py` renames every path and file to it, pins the guideline release it
ships with, and starts a git repository with nothing staged. It uses the
standard library only.

<!-- agents-only
The name's forms: snake for the package, the logins, and the paths; kebab
for distributions, domains, and cloud resources; `NAME_` for the
environment; Title for prose. A two-word name is a first-class case, so
keep every `acme` in a new file where its form is plain from its neighbors:
`acme-` for a resource, `acme_` or `acme.` for code.
-->
