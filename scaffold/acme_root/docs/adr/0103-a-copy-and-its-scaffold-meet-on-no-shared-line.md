# ADR 0103: A copy and its scaffold meet on no shared line

**Status**: accepted (2026-10-14)

## Context

A copy takes each later release of the scaffold as a merge from its
`scaffold` branch. The merge stops on a conflict wherever the copy's
change and the release's change edit the same lines, or lines next to
each other. Most such stops come from a file's shape, not from work that
overlaps: a list every namespace appends to, so the copy's entry and the
release's both land at its end; a skill the copy writes its own kinds
into; a one-line `.PHONY` every new target edits; an ADR the copy edits
in place; a count two changes both bump. The roots stay typed and built
whole at boot ([ADR 0007](0007-roots-built-whole-at-boot.md)), so
the answer is a shape that keeps the two apart, never a discovery of
namespaces at run time.

## Decision

**A marker line in every root and registry.** Each composition root and
each list a copy adds to holds the scaffold's entries, then the line
`# A copy adds its entries below this line.` (`//` in TypeScript,
`<!-- -->` around it in Markdown and `llms.txt`), then the copy's own:
the object model's root and its builders, the storage root and its two
impls, the services root and its impl, the gateway's resolvers,
`HOSTED`, `TABLE_ROLES` and `TABLE_SCOPES`, the work and orchestration
kinds, the resource and waiter hooks, the worker's handlers, steps, and
purges, the portal's keys, routes, and realtime table, the stage
construction sites CTX-26 and its test enumerate, the session-scope
test's two lists of the system scope's exceptions, each audience of
`llms.txt`, and the Makefile's buckets and local URLs. A Markdown table
ends at the marker, so the spec's Substitutions and Deviations each
hold the scaffold's table, the marker, then the copy's rows in a table
of its own under the same header. Each entry sits on its own line with a trailing comma,
so the formatter keeps the list exploded, and the scaffold's are sorted
wherever their order carries no meaning. `build_managers` makes one
builder call per manager. A test merges, with `git merge-tree`, an
entry above every marker against one below it
([`test_registry_markers.py`](../../infra/tests/test_registry_markers.py)).
A test of a root reads the root's own getters and fields, never a list
of its entries.

**A rule that names methods is declared beside them.** A storage method
that takes no tenant carries `@tenantless`, a manager method that takes
no stage `@stageless`, and a transition `@transition`, all from
`acme.om.context`, on its interface. The fence's tests and arch-check
(CTX-12) read the declaration; no list in `pyproject.toml` or in a test
names the methods. A contract suite names the interface its cross-tenant
cases cover (`INTERFACE`), and the test gathers the suites itself.

**Kinds are registries.** A work kind is a spec: its name, its payload,
the permission that asks for it, and its lane
([`kinds.py`](../../om/src/acme/om/work/kinds.py)). An orchestration
kind is its name and its input shape
([`kinds.py`](../../om/src/acme/om/orchestrations/kinds.py)), and the
worker keys its step by the name. A resource kind and a waiter kind are
their hooks, registered at the root by name, and a resource kind's hooks
carry the shape of its ask (`ASK`). The scaffold's own kinds are entries
a copy never edits; a copy names its kinds in its namespace's own module
and registers them below the marker. A registry refuses a name
registered twice, the lease manager refuses at its build hooks it could
not serve, and a start or an ask of a kind nobody registered is refused.
A kind is a string held to a pattern on every row and on the wire, not a
closed enum, so a stored value never changes when a kind is added.

**The ops skills read one signals file per namespace.** What an
investigation, a watch, or a root cause reads of one namespace (its
kinds, its events, its failure signatures) is in that namespace's own
signals file, shared by the three skills
([the signals](../../.agents/skills/_shared/signals/README.md)), and a
copy adds a file for each namespace of its own. The sentences a test
pins are in one test file per skill.

**Nothing a branch routinely adds is counted or enumerated.** A make
target declares `.PHONY` on the line above it. An index document points
at the table, the folder, or the code that holds a list, and does not
repeat it. No document states a count a new entry would bump.

**A copy is formatted as it is rendered.** A name longer or shorter
than `acme` moves Python lines past the formatter's limit or back within
it, so `scaffold/new.py` formats the render by the ruff its `uv.lock`
names before the base is committed, and a later render, made by its
source's own `new.py`, is formatted the same way. The copy's main and
its `scaffold` branch then lay out each line alike, and its first setup
rewrites nothing.

**The contract suites import as a local folder.** `ruff.toml` puts
`contracts` in an import block of its own, so a copy under another name
sorts those imports as the scaffold does.

**A copy never edits an ADR its base gave.** It records what it extends
in an ADR of its own, which cites the base's by number and title
([the decisions' README](README.md)).

**The tenancy tests follow the delegates.** The tenancy manager's unit
tests and the tenancy storage contract are one module per delegate:
sign-in, members, credentials, and the org, beside the manager's own.

## Consequences

A copy's change to a root, a registry, a skill's signals, the Makefile,
the knowledge map, the spec's tables, or its own ADRs lands on lines the
scaffold's next release does not touch, and the move merges them. A conflict that remains is work that
overlaps, which is the one kind a person should read.

The lines above a marker are the scaffold's. A copy that edits one of
them, or puts its entry above the marker, meets the release there again.

A method whose declaration is wrong is caught both ways: one that takes
`org_id` first and is declared `@tenantless` fails the fence's test and
CTX-12, and one that takes no tenant without the declaration fails them
too.
