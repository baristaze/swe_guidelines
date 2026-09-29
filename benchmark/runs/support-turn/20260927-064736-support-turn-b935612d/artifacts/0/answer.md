**First, before diagnosing:** decide whether to roll back. Check whether the deploy included a migration or other irreversible change. If not, and rollback is cheap, roll back now. It stops the bleeding and proves causation in one move. If rollback isn't safe or the tenant impact is small, keep the deploy and debug, and tell support/the account owner that you're on it.

Then, in order:

**1. Read the actual error.** Pull logs and traces filtered to tenant + endpoint + 5xx. Get the stack trace and exception type.
- *Rules out:* guessing. It tells you whether the failure is in the handler, middleware, serialization, DB, or a downstream call.
- Also confirm the 500 comes from the app and not from a gateway, proxy, or WAF rewriting something upstream. If the app never logged the request, the problem is in front of it.

**2. Pin down scope and timing.** Plot error rate for this tenant/endpoint against the deploy timeline. Check whether every request fails or only some, and whether all hosts, pods, and regions are affected or only some.
- *Starts exactly at the deploy:* rules out coincidence.
- *Only on some hosts:* suggests a partial or canary rollout with this tenant pinned to new instances, or one bad node.
- *Only some requests:* the failure depends on the data or payload, not the tenant as a whole.
- *Started slightly before or after the deploy:* look at a tenant-side change (new client version, new integration) or a scheduled job.

**3. Diff the whole deploy, not just the endpoint.** "Endpoint code didn't change" is rarely the full picture. Check:
- lockfile/dependency bumps (serializers, ORM, HTTP client, JSON/date libraries)
- shared middleware: auth, tenancy resolution, rate limiting, validation
- model/schema definitions the endpoint reads
- migrations
- config, env vars, secrets, feature-flag defaults
- infra/base image changes

*Rules out or identifies* the changed code path the endpoint actually runs through. This usually produces the suspect list.

**4. Find what's different about this tenant.** Compare it with a healthy tenant on the same endpoint:
- **Data shape:** nulls in fields that were always set, legacy records, odd encodings or unicode, very large collections, deprecated enum values. A tightened validator or changed serializer typically breaks only on old or weird data.
- **Flags and config:** feature flags, plan/entitlements, per-tenant overrides, SSO/auth config.
- **Placement:** which shard, DB, schema, cell, or region they're on.
- Replay one of their failing requests against staging with a copy of the relevant data.
- *Rules out* "one tenant" being random. There is almost always a concrete property that selects them.

**5. Check tenant-scoped state the deploy touched.** Classic culprits:
- **Per-tenant schema or shard migration:** did it fail or get skipped for this one? Check the migration table on their shard.
- **Cache format change:** the new code can't deserialize old cached objects. Other tenants' entries expired or were rebuilt, but this tenant's long-TTL entry is still stale. Test by flushing this tenant's keys for that endpoint.
- **Their shard/DB health:** locks, a replica with lag, connection limits. It could coincide with the deploy, or the new code could issue a query that's slow on their data volume.
- *Rules out* problems in stored state versus problems in code.

**6. Check the tenant side.** Compare their request payloads and headers before and after the start time. If they changed clients at the same moment, the deploy may be a red herring. Or the deploy removed tolerance for something they always sent.

**Fix and close out:**
- Apply the narrowest safe fix: roll back, flush the cache, rerun the migration, flip the flag, or patch the null handling.
- Verify the tenant's error rate returns to baseline.
- Update the tenant or account team.
- In the postmortem, cover the per-tenant error-rate alerting gap. An aggregate error rate hides exactly this failure, and you want to have found it before the customer did.
