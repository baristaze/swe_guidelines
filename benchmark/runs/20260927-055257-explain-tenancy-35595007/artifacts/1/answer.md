The guideline holds the tenant fence with two independent fences. The **first** is the `org_id` predicate in every query, and the business layer relies on it. The **second** is a Postgres row-level security policy on every tenant table, taken by default, which catches a predicate that went missing. Tests prove each one, and a deliberate breach proves the tests.

**The first fence: the predicate.** The Storage Layer, Storage Principles:

> The predicate in the query is the fence, and the cases that present another tenant's identifier (see Tests) are its evidence.

Namespace Shape says the fence lives in one place: "the `WHERE` clause of the query." A signature that takes `org_id` only shows the tenant was offered, not that the query uses it. The only exceptions to tenant-first are listed by name: the cross-tenant sweeps, the five lookups that run before an identity is known, and the operator plane's idempotency-marker methods. `arch-check` holds the code to that list.

**The second fence: the policy.** The Storage Layer, The Second Fence:

> **Principle:** Every table declares its tenancy scope, and the database carries the policy that scope implies. The predicate in the query is still the fence the business layer relies on. The policy is what catches the predicate that went missing.

How it holds:

- **Scope per table.** Each table declares a tenancy scope (`system`, `org`, `identity` or `both`) in one map beside the role map. The scope decides the policy's shape.
- **One funnel sets the tenant.** The base class's session helper sets `app.org_id`, `app.user_id` and `app.identity_id` using transaction-local `set_config`. A pooled connection therefore hands nothing to the next caller.
- **One policy per table.** Each policy is `FOR ALL`, with `USING` and `WITH CHECK` the same expression. Every tenant table has both `ENABLE` and `FORCE ROW LEVEL SECURITY`, so the table owner is bound too.
- **It fails closed.** `NULLIF` turns an unset tenant into NULL, so "a transaction that named no tenant fails closed: a read returns nothing and a write is refused."
- **Three logins, none privileged.** None is a superuser or has `BYPASSRLS`, because "a fence behind one is a drawing."
  - The migration login owns the schema.
  - The runtime login owns nothing and only has DML rights.
  - The system login alone can use the `EMPTY_UUID` system scope. A statement injected into a request can name that scope and still read nothing.

**What proves it.** The guideline asks for four pieces of evidence:

1. **Cross-tenant cases** (Cross-Cutting Conventions, Tests). A storage method is called under one tenant with another tenant's identifier, and "finds nothing and changes nothing." The cases cover reads, writes, lists, pages, bulk writes and early-exit paths. Every new storage method arrives with its case, run over memory and over the engine.
2. **Login assertions** (The Second Fence). On each live connection, a test asserts three things: `current_user` is neither superuser nor `BYPASSRLS`, the runtime login owns no table, and the runtime login naming the system scope reads nothing. "Those tests are what make the fence real instead of a claim."
3. **Policy-presence check.** The check that the ORM and the schema agree can't see policies. So a second integration test reads `pg_class` and `pg_policies` for every table in the scope map and asserts that the migrated database holds what each table declares.
4. **The negative control** (Tests). Take a tenant predicate out of one query and run the suite twice:
   - With the policy live, the suite stays green. That is the second fence holding.
   - With the policy turned off (as the migration login), the suite goes red. That shows the suite would see the breach.

   Both runs are recorded, because "a negative control nobody ran is a claim, not evidence."

**Lenses a reviewer would apply:**
- `CTX-09`, tenancy enforced in storage
- `CTX-12` and `CTX-13`, the listed exceptions and the system scope
- `CTX-30`, the cross-tenant case
- `CTX-31`, the deliberate breach
- `CTX-32`, the funnel sets the scope
- `STO-28`, the scope and the policy match
- `STO-33`, a data migration lifts `FORCE` inside one transaction

For a review of real code, run `/swe-guidelines:arch-review-context` and `/swe-guidelines:arch-review-storage`.
