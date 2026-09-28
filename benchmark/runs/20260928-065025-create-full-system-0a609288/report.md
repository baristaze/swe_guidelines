# Benchmark run 20260928-065025-create-full-system-0a609288

Scenario `create-full-system`, runtime `vm`, 1 repeat(s), guideline `7e03524babada577472727857dbc0497f4ef05ed`.

Started 2026-09-28T13:50:26Z, finished 2026-09-28T15:20:48Z.

Optional groups taken: `extras`.

This run resumed the run `20260927-233327-create-full-system-11435123`, at `benchmark/runs/20260927-233327-create-full-system-11435123`, after its phase `scaffold`. Each repeat started from that phase's milestone, and the phases up to it are that run's, carried.

That run's checkout, `3009fa9d25b987f0e553daa83bc521a68c4aa42b`, ran the carried phases; this run's checkout ran the rest.

## Scores

| Repeat | Provider | Model | Effort | `guideline` | `reference` | Weighted | Tool calls | Latency (s) | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | anthropic | `claude-opus-5-5` | high | 83 | 80 | 81.2 | 48 | 165.3 | ok |
| 0 | openai | `gpt-6-sol` | high | - | - | - | 73 | 72.2 | error |
| 0 | gemini | `gemini-3.1-pro-preview` | high | 100 | 95 | 97.0 | 133 | 250.6 | ok |
| 0 | xai | `grok-4.7` | high | 82 | 78 | 79.6 | 378 | 197.7 | ok |

## Summary

| Provider | Mean | Min | Max | Stdev | n |
| --- | --- | --- | --- | --- | --- |
| anthropic | 81.2 | 81.2 | 81.2 | - | 1 |
| gemini | 97.0 | 97 | 97 | - | 1 |
| xai | 79.6 | 79.6 | 79.6 | - | 1 |

Overall mean, of the weighted scores: 85.9.

### References

| Reference | Weight | Mean | anthropic | gemini | xai | Min | Max | n | Gaps high / medium / low |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `guideline` | 0.4 | 88.3 | 83.0 | 100.0 | 82.0 | 82 | 100 | 3 | 1 / 7 / 2 |
| `reference` | 0.6 | 84.3 | 80.0 | 95.0 | 78.0 | 78 | 95 | 3 | 0 / 4 / 6 |

The harness weighs each judgement's scores: 0.4 * `guideline` + 0.6 * `reference`.

Spread over the repeats: means 85.9; min 85.9, max 85.9, stdev -.

Note: a Claude subject is judged by a panel that includes Claude (anthropic); read its score beside the other providers' before trusting the mean.

### Not answered

- `openai`: gpt-6-sol: RateLimitError: Error code: 429 - {'error': {'message': 'Rate limit reached for gpt-6-sol in organization org-JM1wKFuLVoBA7dR8Ak8jTzDV on tokens per min (TPM): Limit 500000, Used 351145, Requested 149984. Please try again in 135ms. Visit https://platform.openai.com/account/rate-limits to learn more.', 'type': 'tokens', 'param': None, 'code': 'rate_limit_exceeded'}}

## Spend

Tokens as each provider billed them: the output includes the reasoning. Dollars at the list
prices in `models.yaml`, every input token priced as uncached input.

| Who | Input tokens | Output tokens | Of which reasoning | Cost (USD) |
| --- | --- | --- | --- | --- |
| judge `anthropic` | 1,795,354 | 11,713 | 3,834 | $7.4157 |
| judge `gemini` | 2,938,480 | 11,587 | 6,877 | $6.0160 |
| judge `openai` | 817,497 | 2,509 | 368 | $1.6601 |
| judge `xai` | 7,160,013 | 11,621 | 75 | $14.3898 |
| subject | 50,662,435 | 287,910 | 65,690 | $58.2832 |

Total: $87.7648.

## Phases

Each session of the subject: how it ended, the bound that ended it, its turns, its wall time, what it spent,
and each model it used with that model's cost, as Claude Code's result reports them. A carried phase ran
in the run this one resumed.

| Repeat | Phase | Session | Status | Cap | Turns | Wall (s) | Cost (USD) | Models | Checkpoint |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | scaffold | fresh | incomplete (carried) | - | 500 | 9739.4 | $82.5281 | `claude-opus-5-5` $82.5281 | `2fc0ead5a450` |
| 0 | mvp | fresh | ok | - | 180 | 3333.8 | $22.6312 | `claude-opus-5-5` $22.6312 | `6d87c1cba31a` |
| 0 | review | fresh | ok | - | 14 | 630.8 | $20.3509 | `claude-opus-5-5` $20.3509 | `b615cef1e5c7` |
| 0 | close | fresh | ok | - | 47 | 1145.6 | $15.3011 | `claude-opus-5-5` $15.3011 | `c5389b394de8` |

## Output

The output's last commit, archived whole; `results.json` has each zip's SHA-256.

- repeat 0: `artifacts/0/output.zip`, 743 file(s), 1,004,338 bytes; manifest `artifacts/0/MANIFEST.txt`
- repeat 0, the milestone of scaffold: `artifacts/0/milestones/scaffold/output.zip`, 533 file(s)
- repeat 0, the milestone of mvp: `artifacts/0/milestones/mvp/output.zip`, 726 file(s)
- repeat 0, the milestone of review: `artifacts/0/milestones/review/output.zip`, 726 file(s)
- repeat 0, the milestone of close: `artifacts/0/milestones/close/output.zip`, 743 file(s)

## Gates

The scenario's gates, run on the final tree. They are recorded beside the scores and cap none.

- repeat 0: `make check` passed
- repeat 0: `make test-integration` passed

## Gaps

### `guideline` (weight 0.4)

- **high** DEL-16 (xai, repeat 0): The operator plane is served only as API routes under /v1/admin. There is no apps/admin console: no separate origin bundle, no shared design kit, and no operator screens. Terraform reserves admin.<base> as an allowed origin and a public name, but only the portal static site is deployed, so the reserved origin serves nothing. In the output: `apps/`. In the reference: `lenses/delivery.md DEL-16; architecture.md The Operator Console`. Fix: Add apps/admin sharing the portal's stack, tokens, sign-in, and API client, on its own origin and bundle, with routes only under /v1/admin and no socket, and deploy it at admin.<base>.
- **medium** DEL-39 (anthropic, repeat 0): The per-request log line does not carry the request id. RequestIdMiddleware resets request_id_var in its finally block before it calls logger.info, so the correlation filter logs request_id null. Session 1 stopped on exactly this at make test-telemetry, and the two later sessions left it unfixed. In the output: `services/api/src/acme/services/api/gateway/observability.py`. In the reference: `lenses/delivery.md DEL-39; services/api/src/tadas/services/api/gateway/observability.py and tests/test_access_log.py in the reference`. Fix: Emit the access log line before request_id_var.reset(token), add an access-log test that asserts the request id, and run make test-telemetry.
- **medium** ASY-12 (anthropic, repeat 0): The payment webhook settles the pledge inside the request instead of going onto Queues.WEBHOOKS for a consumer. The deviation is recorded in ADR 0010 and softened (one CAS write plus outbox rows, UUID v5 keys), but a high-severity lens is still bypassed, and dead letters rely on the provider's dashboard rather than an audit entry plus a metric. In the output: `services/api/src/acme/services/api/routers/support.py`. In the reference: `lenses/async.md ASY-12; Infrastructure, Queues`. Fix: Enqueue the verified delivery on Queues.WEBHOOKS and settle it in a worker consumer that dedupes on the delivery key, audits dead letters and counts them.
- **medium** ASY-19 (anthropic, repeat 0): The notifier worker was copied from the maintenance loop with the sweep removed. The guideline says every worker runs the idempotent sweep on its own timer, with no single designated runner. In the output: `workers/notifier/src/acme/workers/notifier/loop.py`. In the reference: `lenses/async.md ASY-19; Worker Roles, Maintenance Without a Scheduler`. Fix: Run the same sweep timer in the notifier's loop (it is idempotent and serialized by the database), or share the loop so that every worker role runs it.
- **medium** ASY-08 (anthropic, repeat 0): The whole sealed report, attachments included, is stored as base64 in a Postgres text column (sealed_reports.ciphertext). The guideline sends blobs to buckets keyed by a fixed enum and moved by presigned URLs. In the output: `om/src/acme/om/reports/types/sealed_report.py`. In the reference: `lenses/async.md ASY-08`. Fix: Keep the report header ciphertext in the row, and put attachment ciphertexts in a bucket under a fixed bucket enum with presigned upload and download URLs.
- **medium** CTX-24 (xai, repeat 0): Operator and tenant operations share one TenancyManagerInterface and one impl. The operator writes are stamped through operator_outbox_row rather than outbox_row, but the plane is not a separate manager the way the guideline's operator plane and the reference realize it. In the output: `om/src/acme/om/tenancy/manager.py`. In the reference: `reference om/src/tadas/om/tenancy/operator.py; lenses/context.md CTX-24`. Fix: Split the operator operations into a TenancyOperatorManagerInterface in its own module, and keep the tenant manager free of OperatorContext.
- **medium** STO-32 (xai, repeat 0): Soft-deleted Journalist, Follow, and Org rows have no retention and no purge. The sweep purges outbox rows, work items, markers, tickets, and sessions, but the hard delete of a soft-deleted row never exists, and there is no erasure path that redacts an erased person's audit entries. In the output: `workers/maintenance/src/acme/workers/maintenance/loop.py`. In the reference: `lenses/storage.md STO-32, STO-34`. Fix: Give each soft-deletable entity a retention, purge it from the maintenance sweep as the only hard delete, and redact personal audit fields on erasure.
- **medium** STO-31 (xai, repeat 0): Backups are declared (RDS backup_retention_period) but no runbook records a restore rehearsal or how a role restored earlier than its siblings is reconciled from the outbox. In the output: `docs/runbooks/`. In the reference: `lenses/storage.md STO-31; reference docs/runbooks/restore.md`. Fix: Add a restore runbook that records the rehearsal, the break-glass procedure, and the outbox reconciliation step.
- **low** DEL-32 (anthropic, repeat 0): The local stack sends traces straight to Jaeger with no OpenTelemetry collector, while the deployed tasks run a collector sidecar, so local and deployed collection paths differ. In the output: `deployment/local/docker-compose.yml`. In the reference: `lenses/delivery.md DEL-32`. Fix: Add a local otel-collector that fans traces and metrics out to Jaeger and Prometheus, as the deployed task template does.
- **low** DEL-17 (xai, repeat 0): No CLI app. The guideline's layout includes apps/cli as a thin REST client; the product has none. In the output: `apps/`. In the reference: `lenses/delivery.md DEL-17`. Fix: Add apps/cli only if a command-line caller is part of the product; otherwise record the omission as a deviation.

### `reference` (weight 0.6)

- **medium** ASY-12 (anthropic, repeat 0): The reference sends its processor webhook onto Queues.WEBHOOKS through a WebhooksService and consumes it in the maintenance worker. The output settles inline in a domain router's route. In the output: `services/api/src/acme/services/api/routers/support.py`. In the reference: `services/api/src/tadas/services/api/routers/webhooks.py; services/api/src/tadas/services/api/services/impl/billing.py (queues.send(Queues.WEBHOOKS, ...)); workers/maintenance/src/tadas/workers/maintenance/deliveries.py`. Fix: Add a dedicated webhooks router and service that enqueue the verified delivery, with a worker consumer for it.
- **medium** DEL-39 (anthropic, repeat 0): The reference has an access-log test that pins the request id on the per-request line. The output has no such test, and its line carries a null request id. In the output: `services/api/src/acme/services/api/gateway/observability.py`. In the reference: `services/api/tests/test_access_log.py`. Fix: Fix the reset ordering and add an access-log test like the reference's.
- **medium** CTX-24 (xai, repeat 0): The operator plane is not split into its own manager. The reference keeps TenancyOperatorManagerInterface, WorkOperatorManagerInterface, and BillingOperatorManagerInterface apart from the tenant managers; the output folds operator operations into TenancyManagerInterface. In the output: `om/src/acme/om/tenancy/manager.py`. In the reference: `om/src/tadas/om/tenancy/operator.py`. Fix: Extract the operator operations into a separate interface and impl, wired beside the tenant manager in build_managers.
- **medium** STO-31 (xai, repeat 0): No restore runbook. The reference records backups, the break-glass restore, the rehearsal, and outbox reconciliation in docs/runbooks/restore.md; the output has only the tenant-isolation runbook. In the output: `docs/runbooks/`. In the reference: `docs/runbooks/restore.md`. Fix: Add the restore runbook in the reference's shape, including the rehearsal record.
- **low** ASY-19 (anthropic, repeat 0): The output adds a second worker whose loop is a hand copy of the maintenance loop without the sweep. The reference keeps one worker role and routes kinds to handler modules within it. In the output: `workers/notifier/src/acme/workers/notifier/loop.py`. In the reference: `workers/maintenance/src/tadas/workers/maintenance/ (handler.py routing to deliveries.py, slack_posts.py, reminders.py)`. Fix: Either host SEND_NOTIFICATION as a handler in the maintenance worker, or share one loop implementation that keeps the sweep.
- **low** DEL-03 (anthropic, repeat 0): The Terraform modules put variables and outputs in main.tf and have no tftest.hcl coverage. The reference splits variables.tf and outputs.tf per module and tests the service, alarms, dashboard and static_site modules. In the output: `deployment/terraform/modules/`. In the reference: `deployment/terraform/modules/service/tests/*.tftest.hcl; modules/alarms/tests; modules/static_site/tests`. Fix: Split the module interfaces into variables.tf and outputs.tf, and add terraform test files for the service, observability and static_site modules.
- **low** DEL-32 (anthropic, repeat 0): The reference's local stack includes an otel-collector config. The output's local stack does not. In the output: `deployment/local/docker-compose.yml`. In the reference: `deployment/local/otel-collector/collector.yml`. Fix: Add the local collector service and its config.
- **low** network (gemini, repeat 0): Service implementations are located in `impl/` instead of `services/impl/`, deviating structurally from the reference's network module layout. In the output: `services/api/src/acme/services/api/impl`. In the reference: `services/api/src/tadas/services/api/services/impl`. Fix: Move the service implementation files from `services/api/src/acme/services/api/impl` into `services/api/src/acme/services/api/services/impl/` to mirror the reference structure.
- **low** ASY-19 (xai, repeat 0): The outbox row omits the relay's attempt accounting. The reference's OutboxRow carries attempts, next_attempt_at, last_error, and failed_at so a row that will not relay becomes a dead letter; the output tracks only done_at. In the output: `om/src/acme/om/outbox/types/__init__.py`. In the reference: `om/src/tadas/om/outbox/types/row.py`. Fix: Add the attempt fields and have the sweep spend an attempt and dead-letter a row past the bound.
- **low** DEL-17 (xai, repeat 0): No CLI distribution. The reference ships apps/cli as a thin REST client; the output has only the portal. In the output: `apps/`. In the reference: `apps/cli/`. Fix: Add the CLI only if this product needs one; the reference has it because its product does.

## Strengths

- (anthropic, `guideline`) om/src/acme/om/<namespace>/ follows the prescribed namespace shape (manager interface, impl, storage interface with Postgres and memory impls, tables, types) for journalists, follows, updates, support, reports, work, tenancy, outbox, events, audit and idempotency.
- (anthropic, `guideline`) om/src/acme/om/storage/impl/pg_base.py funnels every statement through one session that routes by role, picks the system login for EMPTY_UUID and sets app.org_id/user_id/identity_id per transaction; om/migrations/sql/** enable and FORCE row-level security on every tenant table, and om/tests/integration/test_database_fence.py checks each policy against its declared scope.
- (anthropic, `guideline`) om/src/acme/om/follows/impl/manager.py and reports/impl/manager.py follow authorize, verify, copy, write: the outbox rows land in the same transaction, a relay runs after commit, and a replayed create returns the row as stored.
- (anthropic, `guideline`) workers/notifier/src/acme/workers/notifier/handler.py is idempotent on the item's key through per-address work receipts plus a provider idempotency key, and loop.py claims within capacity, renews leases, cancels on Conflict and hands work back on shutdown.
- (anthropic, `guideline`) Every external provider (WorkOS, Stripe, Postmark/Resend, Twilio, the malware scanner) sits behind an interface with a twin and a real client in integrations/src/acme/integrations/*, as ADR 0005 records.
- (anthropic, `guideline`) Decisions are ADRs in docs/adr/0001-0012, and deviations quote the lens they depart from (0010 quotes ASY-12, 0012 covers the OM-12 exception); arch-check exceptions are scoped in pyproject.toml.
- (anthropic, `guideline`) services/api/tests/test_loop_follow_and_support.py, test_loop_tip.py and test_loop_inbox_and_reply.py drive each MVP loop end to end over Postgres with the real notifier loop and on-device sealing, including cross-desk negative assertions.
- (anthropic, `guideline`) The delivery chain is complete: .github/workflows/deploy-production.yml promotes digests and never rebuilds, release.yml moves release by fast-forward only, and deployment/terraform has bootstrap and environment roots per account with narrow smoke and investigate roles.
- (anthropic, `reference`) The repository layout matches the reference role for role: apps/portal, clients/python, deployment/{docker,local,terraform,cloud}, infra, integrations, om (with migrations/sql up/down), ops (audit, stress, signals, traffic), services/api, workers, docs/adr, specs, and .claude/skills.
- (anthropic, `reference`) services/api/src/acme/services/api/ has the reference's gateway/routers/services/types/realtime split, with gateway modules for auth, admin, errors, idempotency, ratelimit and webhooks.
- (anthropic, `reference`) deployment/terraform/{bootstrap,environments}/{staging,prod} plus modules mirror the reference's one-account-per-environment layout, and the account IAM carries the same build/plan/deploy/smoke/investigate roles.
- (anthropic, `reference`) om/tests/contracts/* run the same contract cases over memory (unit) and Postgres (integration), including cross-tenant cases, the way the reference's storage suites do.
- (anthropic, `reference`) apps/portal/src/features/*/ uses the reference's view / view-model hook / model-module triplet, with a query layer, a Zustand store and a typed realtime router.
- (gemini, `guideline`) OM properly uses FrozenMapping with validate_default=True for all mapping fields (OM-17).
- (gemini, `guideline`) The storage layer strictly keeps migrations as SQL pairs bounded by roles, correctly placed with ALEMBIC config matching STO-18.
- (gemini, `guideline`) The worker infrastructure correctly opens loops and sets health bounds within the defined capacities.
- (gemini, `guideline`) Context objects properly encapsulate permission requirements, utilizing ctx.require() across manager interfaces (CTX-21, CTX-08).
- (gemini, `guideline`) Integration tests effectively exercise all three MVP workflows as requested in the instructions.
- (gemini, `reference`) The OM test directory closely matches the reference structure, sibling to contracts and unit scopes.
- (gemini, `reference`) The deployment Dockerfiles and Terraform environments maintain identically structured configurations (DEL-03, DEL-10).
- (gemini, `reference`) The React frontend app follows identically the reference feature-based structure with TanStack Query and Zustand (DEL-13).
- (xai, `guideline`) The object model is one distribution under om/ with the prescribed mixins, frozen root, FrozenMapping, namespace shape, and manager-owned fields, and updates copy from the stored row excluding provenance and manager-owned fields.
- (xai, `guideline`) Tenancy has both fences: every query filters by tenant, the session funnel sets app.org_id with set_config local to the transaction, and every table carries the policy its scope map declares, with a recorded negative-control run.
- (xai, `guideline`) Context stages, scopes, the operator gate, credential prefixes, socket tickets, session revocation, and the sign-in throttle are implemented where the guideline puts them, and the isolation contract cases cover both storage impls.
- (xai, `guideline`) Workers claim within capacity, renew leases, cancel on Conflict, hand work back before going offline, and the maintenance sweep relays the outbox and purges the bookkeeping rows; durable work is a row with a per-tenant idempotency key.
- (xai, `guideline`) The network edge has one error envelope, edge idempotency with secret stripping, health outside /v1, a committed OpenAPI document, and a generated client behind a facade; the portal keeps the bearer in session storage and Terraform sends a CSP.
- (xai, `reference`) The repository, package, and namespace layout match the reference: one OM distribution, src layout, storage and infra roots with memory and configured impls, a services root, and workers sharing the service project shape.
- (xai, `reference`) The container boots in the same order (settings, logging, tracing, storage, infra, managers, services) and tests construct the same container over the memory storage root and local infra root.
- (xai, `reference`) Deployment matches the reference's shape: bootstrap and environment roots, one account per environment named in one file, central two-stage non-root Dockerfiles, promotion by digest, and the same workflow set.
- (xai, `reference`) Operations match: the four roles, the skill set under .claude/skills, the traffic generator and stress scenario, the signals readers, alarms, budget, and autoscaling declared as code.
- (xai, `reference`) Contract cases run over both storage impls and race the named atomic methods, and the three product loops have end-to-end integration tests, which is the reference's testing shape.

## Rationales

- **anthropic**, repeat 0: The output is a thorough realization of the guideline. It has the full namespace shape in om, one storage funnel that sets the tenant scope, forced row-level security on every tenant table, outbox relays in the same transaction, provider twins, a promote-don't-rebuild deploy chain with per-account Terraform, ADRs for every decision, and end-to-end integration tests for all three MVP loops. Points come off for a few real departures rather than broad structural failure. The per-request log line still logs a null request id because the context var is reset before the log call; this was found in session 1 and never fixed. The payment webhook settles inline instead of going through the inbound queue: the deviation is recorded in ADR 0010 but still bypasses a high-severity lens, and the reference routes the same thing through Queues.WEBHOOKS to a worker. The notifier worker drops the sweep that every worker is meant to run, and report ciphertext with attachments sits in a Postgres column rather than a bucket. Against the reference the layout, layering and ops tooling match closely; the differences are the webhook path, a copied worker loop, thinner Terraform tests, and the missing access-log test and local collector.
- **gemini**, repeat 0: The output successfully implements the required MVP, remarkably following the architecture guidelines across all 8 lens groups. The Object Model strictly adheres to immutability rules and correctly defines namespaces (e.g. journalists, reports, follows). Context is correctly propagated with required stages and isolation principles enforced. The storage layer ensures atoms, properly scoped pools, and migrations split exactly as STO-18 instructs. The operations scripts correctly implement the 13 required skills from OPS-11, and the delivery pipeline configures Terraform, Dockerfiles, and GitHub actions consistently.

The only gap observed is in how the network service layer structures its implementation classes compared to the reference. In the reference, the service implementations reside in `services/impl/`, while the output places them one level higher directly in `impl/`. Since the guideline doesn't strictly dictate this exact path nesting, this is merely a structural deviation from the reference rather than a guideline violation. Both scores reflect this exceptionally high degree of compliance.
- **xai**, repeat 0: The output follows the guideline's shape closely: one OM distribution, prescribed mixins, dual storage impls, a session funnel with forced row-level security, context stages, an operator gate, edge idempotency, and workers that claim, renew, and sweep. The largest miss is the operator console: admin.<base> is reserved and /v1/admin exists, but no separate app is built or deployed. Smaller misses are the operator plane sharing the tenancy manager, no purge of soft-deleted rows, and no restore-rehearsal runbook. Against the reference the same structural gaps hold, while domain differences (no CLI, no billing) are not counted.

## Versions

- Checkout: `7e03524babada577472727857dbc0497f4ef05ed`, plugin `0.37.0`, clean.
- Claude Code: `2.1.283 (Claude Code)`.
- Target: `benchmark/fixtures/create-full-system`, sha256 `97bc0dbcd93b5725cdd3030a1b1b5978315ee26ec14725c1f3c641d478933f13`.
- Reference `guideline`: `architecture.md`, `lenses`, `skills` of the checkout, sha256 `5c53f11f4ee7849781e2452f17f4b3f4024e6679a15e1c9025798285907c903e`.
- Reference `reference`: https://github.com/baristaze/tadas at tag `v0.7.0`, commit `82e4d050ef53d5f17139f0f90476eaf157b6ff08`, pins the guideline at `v0.37.0`.

## Paths

- run folder: `20260928-065025-create-full-system-0a609288`
- artifact, repeat 0: `artifacts/0/answer.md`
- artifact, repeat 0: `artifacts/0/workspace/review/report.md`
- artifact, repeat 0: `artifacts/0/output.zip`
- artifact, repeat 0: `artifacts/0/MANIFEST.txt`
- transcript, repeat 0, anthropic: `judgements/0-anthropic.jsonl`
- transcript, repeat 0, openai: `judgements/0-openai.jsonl`
- transcript, repeat 0, gemini: `judgements/0-gemini.jsonl`
- transcript, repeat 0, xai: `judgements/0-xai.jsonl`
- streams: `streams/cli.jsonl`
- results: `results.json`

## Notes

- repeat 0: removed what the subject's Docker made on the other machine: 4 containers (acme-postgres-1, acme-objectstore-1, acme-valkey-1, acme-queue-1), 1 network (acme_default), 4 volumes (acme_objectstore-data, acme_postgres-data, acme_valkey-data, d575efe3063c109cf32da13bdf4e021981df03e7f43bccce76fd81196aebe763)
