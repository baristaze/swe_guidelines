The guideline puts up two fences, and it says plainly which one the code depends on.

**The first fence is the predicate in the query.** *The Storage Layer, Storage Principles*: "Tenancy is enforced on every read and checked on every write. A query filters by `org_id`. An upsert refuses to overwrite a row that belongs to another tenant." *Namespace Shape* adds that "the fence itself exists in one place: the `WHERE` clause of the query." A method signature only shows that the tenant was offered. Methods that legitimately take no tenant are listed by name, and `arch-check` holds the code to that list.

**The second fence is row-level security, and it's on by default.** *The Storage Layer, The Second Fence*:

> **Principle:** Every table declares its tenancy scope, and the database carries the policy that scope implies. The predicate in the query is still the fence the business layer relies on. The policy is what catches the predicate that went missing.

The design has three parts:
- **Scope and policy.** Each table declares a scope: `system`, `org`, `identity` or `both`. It gets one `FOR ALL` policy with `ENABLE` and `FORCE ROW LEVEL SECURITY`, so even the owner is bound by it.
- **One funnel.** Every session opens through a single session helper. It sets `app.org_id`, `app.user_id` and `app.identity_id` with `set_config(..., true)`, so the values die with the transaction.
- **Fail closed.** Because of `NULLIF`, a transaction that names no tenant reads nothing and has its writes refused.

The one deliberate bypass is the system scope, `EMPTY_UUID`. The policy admits it only for the system login, and only for methods listed by name. Three logins reach the database (migration, runtime and system). None of them is a superuser or has `BYPASSRLS`, because "a fence behind one is a drawing."

**Four kinds of test prove it.** The guideline treats each as evidence, not a claim.

1. **Cross-tenant cases** (*Cross-Cutting Conventions, Tests*): "A contract case calls a storage method under one tenant with another tenant's identifier, and asserts that it finds nothing and changes nothing." They must cover reads, writes, the list, the page, the bulk write and the failure paths, over both memory and the engine. Every new storage method arrives with its own case.
2. **A negative control against a deliberate breach.** Take the tenant predicate out of one query and run the suite twice. With the policy live it should stay green, which shows the second fence holding. With the policy turned off (by the migration login) it should fail, which shows the suite can see a breach. "A negative control nobody ran is a claim, not evidence." Both runs are recorded.
3. **Login tests** (*The Second Fence*): on every live connection, `current_user` is neither superuser nor `BYPASSRLS`, the runtime login owns no table, and the runtime login naming the system scope reads nothing. "Those tests are what make the fence real instead of a claim."
4. **A policy-check test.** It reads `pg_class` and `pg_policies` for every table in the scope map and asserts that the migrated database holds what each table declares. This is needed because the ORM-versus-schema check can't see policies.

**Lenses a reviewer would apply:** CTX-09, CTX-12, CTX-13, CTX-30, CTX-31, CTX-32 and STO-28, plus STO-30 for unique keys that lead with `org_id`. `/swe-guidelines:arch-review-context` and `/swe-guidelines:arch-review-storage` run those checks against actual code.
