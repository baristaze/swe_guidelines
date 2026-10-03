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
ships with, and starts a git repository. From a clean checkout of this
repository, the repository's first commit is the copy itself, on the
branch `scaffold` and the main branch: the copy's base. It uses the
standard library only.

## Move it forward

A copy takes a later release by merging it. `base.py` commits the
scaffold at that release onto the copy's `scaffold` branch, renamed as
the copy was, and `git merge scaffold` brings in what the scaffold
changed since the copy's base, keeping what the copy changed:

```bash
python3 scaffold/base.py v0.49.0 --repo ~/code/pressroom
cd ~/code/pressroom && git switch -c scaffold-v0-41-0 && git merge scaffold
```

`base.py` fetches one tarball of the release and never clones. A
layer, a repository whose own scaffold builds on this one, takes the
`scaffold/` folder unchanged with `--layer` and merges it the same way.
`/swe-guidelines:arch-upgrade-scaffold` makes the whole move: it grafts
a copy made before its base was recorded, merges, resolves what the
merge leaves, and runs the gates. The move merges into the main branch
with a merge commit, never a squash, since the merge is what records the
base.

<!-- agents-only
The name's forms: snake for the package, the logins, and the paths; kebab
for distributions, domains, and cloud resources; `NAME_` for the
environment; Title for prose. A two-word name is a first-class case, so
keep every `acme` in a new file where its form is plain from its neighbors:
`acme-` for a resource, `acme_` or `acme.` for code.
-->
