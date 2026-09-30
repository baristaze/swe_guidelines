## scaffold

**Name:** `free_journalism`, taken from the arguments.

**Guideline:** `0.45.0`, or a later snapshot of main.

`free_journalism` is copied and green, the product's first decisions are recorded as ADRs 0082–0086, and the `journalists` namespace with its `Journalist` entity runs from the database up to a portal screen. Every gate passed on its first run, so none was rerun.

## Copy

```
935 files in …/workspace/0/free_journalism as free_journalism, pinned at guideline v0.45.0
base: not recorded, since this is not a clean checkout of the guideline; the first move grafts it
```

Because no base was recorded, the repository has no first commit. `git status` therefore lists every copied file as untracked; `arch-upgrade-scaffold` grafts the base on its first move. Docker was running and every port in `.env` was free, so `.env` is an unchanged copy of `.env.example`.

## Files written or changed

- `.github/CODEOWNERS`
- `README.md`
- `llms.txt`
- `HANDOFF.md`
- `docs/adr/0082-free-journalism-on-the-core.md`
- `docs/adr/0083-payments-are-stripes.md`
- `docs/adr/0084-email-is-postmarks.md`
- `docs/adr/0085-sms-is-twilios.md`
- `docs/adr/0086-the-malware-scanner.md`
- `om/README.md`
- `om/src/free_journalism/om/journalists/` (new): `__init__.py`, `README.md`, `manager.py`, `impl/__init__.py`, `impl/manager.py`, `storage/__init__.py`, `storage/impl/__init__.py`, `storage/impl/postgres.py`, `storage/impl/memory.py`, `storage/tables/__init__.py`, `storage/tables/journalists.py`, `types/__init__.py`, `types/journalist.py`, `types/page.py`
- `om/src/free_journalism/om/root.py`
- `om/src/free_journalism/om/storage/root.py`
- `om/src/free_journalism/om/storage/impl/postgres.py`
- `om/src/free_journalism/om/storage/impl/memory.py`
- `om/src/free_journalism/om/storage/roles.py`
- `om/src/free_journalism/om/storage/scopes.py`
- `om/migrations/sql/core/202609301545_journalists.up.sql`
- `om/migrations/sql/core/202609301545_journalists.down.sql`
- `om/migrations/versions/core/202609301545_journalists.py`
- `om/tests/contracts/journalist_storage.py`
- `om/tests/unit/test_journalist_storage.py`
- `om/tests/unit/test_journalist_manager.py`
- `om/tests/integration/test_journalist_storage_postgres.py`
- `om/tests/unit/test_storage_exceptions.py`
- `om/tests/unit/test_session_scope.py`
- `om/tests/unit/test_roots.py`
- `workers/maintenance/src/free_journalism/workers/maintenance/container.py`
- `workers/maintenance/src/free_journalism/workers/maintenance/main.py`
- `services/api/src/free_journalism/services/api/types/journalists.py`
- `services/api/src/free_journalism/services/api/services/journalists.py`
- `services/api/src/free_journalism/services/api/services/impl/journalists.py`
- `services/api/src/free_journalism/services/api/routers/journalists.py`
- `services/api/src/free_journalism/services/api/services/__init__.py`
- `services/api/src/free_journalism/services/api/services/impl/root.py`
- `services/api/src/free_journalism/services/api/gateway/resolve.py`
- `services/api/src/free_journalism/services/api/routers/__init__.py`
- `services/api/README.md`
- `services/api/tests/test_journalists_journalist_api.py`
- `clients/typescript/openapi.json`
- `clients/typescript/src/schema.d.ts`
- `clients/typescript/src/types.ts`
- `clients/python/src/free_journalism/client/schema.py`
- `apps/portal/src/features/journalists/JournalistsPage.tsx`
- `apps/portal/src/features/journalists/useJournalistsVm.ts`
- `apps/portal/src/features/journalists/journalistsModel.ts`
- `apps/portal/src/features/journalists/journalistsModel.test.ts`
- `apps/portal/src/queries/journalists.ts`
- `apps/portal/src/queries/keys.ts`
- `apps/portal/src/app/routes.tsx`
- `apps/portal/src/realtime/router.ts`
- `apps/portal/src/realtime/router.test.ts`
- `apps/portal/src/features/home/HomePage.tsx`

## ADRs

- **0082 – the product on the core.**
  - A journalist's desk is an org: at first the journalist's personal org, and later a team org for a newsroom.
  - A reader is an identity. Their follows and payments are rows in the journalist's org, and the reader sees their own through identity-scoped reads.
  - A source is neither. They need no account, and the platform keeps nothing that identifies them in the clear.
  - The platform never decrypts a report. Removing photo metadata (EXIF) is the sender's device's job.
  - Operators are the trust and safety staff.
  - It lists which core pieces the product uses and the first namespaces: `journalists`, `community`, `support`, `reports`, `notifications`, `safety`.
- **0083 – Stripe.** Checkout for payments and Connect Express for payouts, with the platform fee as Stripe's application fee. It will be reached through `integrations/payments/`, which has a twin.
- **0084 – Postmark**, chosen over Resend. It will be reached through `integrations/email/`, which has a twin, and every send goes through the work queue.
- **0085 – Twilio.** It will be reached through `integrations/sms/`, which has a twin, and only sends to a verified number that opted in.
- **0086 – the malware scanner.** It will be reached through `integrations/scanning/`, which has a twin.
  - Files the platform can read are scanned whole.
  - Report attachments are encrypted, so they are checked on the journalist's device by their digest only. New malware no scanner has seen yet gets through as "unknown".
  - The real provider isn't named. The ADR sets the conditions it must meet and leaves the choice to whoever writes the real client.

As the skill says, each integration lands with the namespace that needs it, so none is written yet.

## Choices made without anyone to ask

- **`Journalist` fields and mixins.** It is `core` role, `org` scope, and `Trackable`.
  - It is not `SoftDeletable`: a profile is removed when its desk's org is deleted and purged.
  - It has no version field and no unique key besides its id.
  - It has no fields that only the manager may set.
  - ADR 0082 records all of this.
- **API update.** `PATCH` refuses an explicit null `display_name` with a 422.
- **Portal screen.** It lists the desk's journalists and has a form to add one. Home links to it. There is no rename control yet, though the API supports renaming.

## Commands

| Command | Outcome |
|---|---|
| `make setup` on the copy | ok |
| `make check` on the copy | ok |
| `make infra-up` | ok |
| `make migrate` | ok (core at `202609301545`) |
| `make openapi` | ok (ran once, before the screen) |
| `make check` | ok (arch-check: 106 rules; 2071 Python tests, 228 portal tests) |
| `make migrate-check` | ok (all four roles in sync) |
| `make test-integration` | ok (312 passed) |

No database gate was skipped.

## Left for you

- **Commit.** Nothing this skill wrote is committed; making the commit is yours.
- **Repository name.** Outside this skill's scope, the placeholder GitHub repository `free-journalism-org/free_journalism` is still in `deployment/cloud/environments.json` and `apps/site/src/site.test.ts`, for you to set.
- **Handoff note.** `HANDOFF.md` records what's done, what was decided, and what's left.

## mvp

The MVP is built in `free_journalism` and all the gates passed on the first run, so none of the three reruns were used. Each of the three loops has an integration test that drives it end to end.

**Gate results**
- `make check`: clean. That covers lint, format, pyright, the guideline's checker (106 rules, no findings), 2,345 unit tests, and the portal and TypeScript client lint, typecheck and tests.
- `make migrate-check`: every database role is in sync.
- `make test-integration`: 358 passed, including the six loop test cases.

**The three loops.** Each test runs the API in-process against the compose Postgres, with the worker beside it, and every person talks through the Python client. Stripe is replaced by a test double that signs its webhooks the way Stripe does.
- **Follow and support** (`test_loop_follow_and_support_integration.py`): a reader reads a journalist's public profile, follows them, and supports them monthly. Only Stripe's signed webhook, applied by the worker, marks the support active; a repeat of the same webhook changes nothing, and a forged one is refused. The journalist's desk sees its followers and supporters, never who they are.
- **A source sends a tip** (`test_loop_send_a_report_integration.py`): a source with no account seals one report on their device for two journalists at two desks. The platform stores one ciphertext plus one wrapped key per recipient. Each journalist opens only their own copy, and one journalist's key cannot open the other's. A named source puts their contact details inside the sealed report, so the platform never holds them in the clear.
- **Inbox and reply** (`test_loop_inbox_and_reply_integration.py`): the journalist filters the inbox by state and tag, opens a report (recorded in the desk's audit trail), and moves it through the states. They send an encrypted reply; the source reads it using the secret their device kept and answers. A wrong secret gets a 404.

**What I built**
- Three new namespaces: `community` (follows), `support` (payments, with Stripe behind an integration that has a test double) and `reports` (deliveries and the reply thread).
- A public key on the journalist profile.
- Encryption on the sender's device, in the Python client's new `sealing` module.
- Portal screens for followers, supporters and the inbox. The browser does not decrypt yet: the Python client is the journalist's decrypting client for now.

**Decisions you should know about**
- **Readers and sources.** Readers act on a journalist's desk as their signed-in selves, not as members of it, and sources act with no account at all. This is recorded in ADR 0087. The guideline permits both cases, and I registered them in the core's own test lists rather than adding exceptions.
- **No new worker kind.** Nothing in the three loops needed background work: the checkout opens during the request, as ADR 0083 says, and Stripe's webhooks go through the worker's existing handler for provider webhooks. So I did not run `/swe-guidelines:arch-scaffold-worker`.
- **How the scaffold skills were used.** I loaded `/swe-guidelines:arch-scaffold-namespace` once and followed its instructions and the entity skill's. I wrote `community` myself, and sub-agents built `support`, `reports`, the Stripe integration, the portal screens and the Python client from those same skill files. I did not call `/swe-guidelines:arch-scaffold-entity` separately for each entity.
- **Viewers cannot open a report.** Every open is written to the desk's audit trail, and a viewer's role cannot write it; they can still list and read deliveries.

**Left out:** everything outside the three loops. That includes unfollow, journalists' updates to followers, notifications, trust and safety, cancellations and refunds, and paying journalists out through Stripe Connect. Two gaps inside the MVP remain: a stored report's ciphertext is not deleted when its last delivery is purged, and there is no live Stripe account or key yet. All of this is listed in `HANDOFF.md`. Nothing is committed.
