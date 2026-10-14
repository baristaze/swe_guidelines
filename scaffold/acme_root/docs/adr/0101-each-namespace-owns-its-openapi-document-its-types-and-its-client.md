# ADR 0101: Each namespace owns its OpenAPI document, its types, and its client

**Status**: accepted (2026-10-14)

## Context

The guideline's "Public Types" puts wire types in one module per
namespace, and "Clients Live in One Place" builds each client from the
committed documents. One document for the whole API puts every
namespace's routes and types in one file, and a product that adds its
own namespaces to the copy adds them there too. Every API change then
rewrites that file and the two generated files beside it, and two
additions that sort next to each other land in the same lines. A name's
owner is a fact of where it is defined, so the files can carry it.

## Decision

**One document per namespace.** `make openapi` writes
`clients/openapi/<namespace>.json` for each hosted namespace, and
`common.json` for what they share
([`openapi.py`](../../services/api/src/acme/services/api/openapi.py)).
A route is in the document of the namespace its tag names. A schema is
in the document of the namespace whose module defines it: a wire type's
`types/<namespace>.py`, or an enum of `om/<namespace>/`. The bases, the
error envelope, the context's roles, and the framework's validation
error are `common`'s. Another document refers to a schema by a file
`$ref`, so each schema is defined once. Inside a document a name does
not repeat its namespace: the file is the namespace, and two namespaces
may each name a type alike.

**The routers follow the namespaces.** A router module is named by its
namespace and tags its routes with it. Its operator routes live there
too, under `/v1/admin/`, as its `operator` router: no router collects
another namespace's routes. The operation id is the route's function
name. The router module's docstring describes its tag, so a document
carries its namespace's routes by area.

**The types follow the documents.** `scripts/openapi_types.sh` writes
one TypeScript file per document under `clients/typescript/src/schema/`,
and the Python schema package, one module per document, where a file
`$ref` is an import of another module. The TypeScript facade is one
module per namespace an app reads, re-exported under its name, so an
app names `tenancy.MeView`. The Python client is the transport and one
module of calls per namespace, composed into `ApiClient`, over a facade
package of the same shape.

**The drift gate fails on any change or new file under `clients/`.**
CI runs `make openapi` and fails when `git status --porcelain -- clients`
prints anything, so a namespace's first document cannot be left
uncommitted.

## Consequences

A change to one namespace rewrites its own document and its own
generated files, and two namespaces' changes never touch one file. A
namespace a product adds to the copy gets its documents and its types by
construction: a router module and a types module named by it, and a
line in `HOSTED`.

A wire type lives in `types/<namespace>.py` or `types/common.py`:
`make openapi` refuses one defined anywhere else, and a route whose tag
is no hosted namespace. The TypeScript generator inlines what a document
refers to in another, so a namespace's `.d.ts` changes when a type it
uses from `common` does.
