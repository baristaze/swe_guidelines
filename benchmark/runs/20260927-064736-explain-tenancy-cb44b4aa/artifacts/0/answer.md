The guideline holds the tenant fence with two independent mechanisms, and it separates evidence from what a signature merely claims.

**Where the fence lives.** *Separation of Layers* gives it to storage: "Managers authorize; storage enforces tenancy." A manager decides whether the caller may act. Storage makes sure that no query reaching the database can cross into another tenant.

**The first fence is the query predicate.** *The Storage Layer, Storage Principles* says: "Tenancy is enforced on every read and checked on every write. A query filters by `org_id`. An upsert refuses to overwrite a row that belongs to another tenant." *Namespace Shape* is blunt about where it sits: "The fence itself exists in one place: the `WHERE` clause of the query." Storage signatures lead with `org_id`. The only methods allowed to skip it are the tenant-less sweeps and the five pre-identity lookups, each listed by name, and `arch-check` holds the code to that list.

**The second fence is row-level security.** *The Storage Layer, The Second Fence*:

> Every table declares its tenancy scope, and the database carries the policy that scope implies. The predicate in the query is still the fence the business layer relies on. The policy is what catches the predicate that went missing.

The shape has four parts:
- **Declared scope.** Every table declares one of `system`, `org`, `identity` or `both`, and each scope has one policy expression. The table gets `ENABLE` and `FORCE ROW LEVEL SECURITY`, so even the owner is bound.
- **One funnel.** The storage base class's session helper sets `app.org_id`, `app.user_id` and `app.identity_id` with transaction-local `set_config`, so a pooled connection never carries one caller's tenant to the next.
- **Fails closed.** `NULLIF` turns an unset or leftover setting into NULL, so "a transaction that named no tenant fails closed: a read returns nothing and a write is refused."
- **Three logins, none a superuser or `BYPASSRLS`.** The migration login owns the schema. The runtime login owns nothing, so it can't drop a policy or turn `FORCE` off. The system login is the only one the system scope (`EMPTY_UUID`) admits, so "a statement injected into a request cannot read across tenants or identities by naming the system scope."

**What proves it.** The guideline says plainly that a signature only shows the tenant was offered: "a method that takes `org_id` and leaves the predicate out of its body passes every check made on signatures." The proof comes from four kinds of test:

1. **The cross-tenant case** (*Cross-Cutting Conventions, Tests*): "A contract case calls a storage method under one tenant with another tenant's identifier, and asserts that it finds nothing and changes nothing." It covers reads, writes, lists, pages, bulk writes and early-return or raising paths, because "a path that skips the query skips the fence with it." Every new storage method arrives with its case, run over both memory and Postgres.
2. **The negative control** (same subsection): take one tenant predicate out of a query and run the suite twice.
   - With the policy on, it stays green: the second fence caught the breach.
   - With the policy off (switched off as the migration login), it must fail, which proves the suite can see the breach.
   - Both runs are recorded, because "A negative control nobody ran is a claim, not evidence."
3. **The policy check** (*The Second Fence*): an integration test reads `pg_class` and `pg_policies` for every table in the scope map and asserts that the migrated database holds the declared policy.
4. **The login assertions** (*The Second Fence*): on each live connection, `current_user` is neither superuser nor `BYPASSRLS`, the runtime login owns no table, and the runtime login naming the system scope reads nothing. "Those tests are what make the fence real instead of a claim."

**Lenses a reviewer applies:** `CTX-09`, `CTX-12`, `CTX-13`, `CTX-30`, `CTX-31`, `CTX-32`, `STO-28` and `STO-33` (a data migration lifts `FORCE` only inside its own transaction and counts rows). For a full pass, run `/swe-guidelines:arch-review-context` and `/swe-guidelines:arch-review-storage`.
