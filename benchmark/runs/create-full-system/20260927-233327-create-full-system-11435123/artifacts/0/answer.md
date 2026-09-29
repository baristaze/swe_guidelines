## scaffold

I stopped at step 9. `make test-telemetry` still failed after one run plus 3 reruns, so step 10 (the sweep for the four common misses and the full architecture review) never ran. Steps 1–8 are done and their gates pass.

**Why it fails:** the request-id middleware clears the request id before it writes each request's log line. In `services/api/src/acme/services/api/gateway/observability.py:98`, `request_id_var.reset(token)` runs in the `finally` block ahead of `logger.info(...)`. So every request log line has `request_id: null`, and the round trip can't find the line by id. I haven't applied the fix, since the stop came first. It's to move the reset below the log line, then rerun step 9 from `make devx-up`. The metric, trace and error-event checks come after the log check in that test, so they haven't been verified yet.

**Guideline version:** 0.37.0.

**Files:** all 533 are untracked in `git status --porcelain --untracked-files=all`. They include:
- the workspace config, `Makefile`, `README.md`, `llms.txt`, `HANDOFF.md`, `docs/` (8 decision records) and the runbooks;
- `.claude/skills/` (15 skills);
- `deployment/`: the local compose stack, the Docker files and the Terraform, 69 files;
- `.github/` (six workflows and `CODEOWNERS`);
- the Python packages `om/`, `infra/`, `integrations/`, `services/api/`, `clients/python/`, `ops/` and `workers/maintenance/`;
- the portal, `apps/portal/` (72 files).

No build output, `.env`, `.data/` or `node_modules` appears in the list.

This session added the portal, the `journalists` namespace and its migration `core/202609281630_journalists`, decision record 0008 for the journalist entity, and `deployment/local/glitchtip/seed.py`.

**Commands and outcomes:**
- Portal (a helper agent built it): `pnpm install`, `generate`, `lint`, `typecheck`, `test` (57 tests) and `build` all passed.
- **Step 6:** the journalist storage, manager and API tests passed (25 tests).
- **Step 7:**
  - `make check` failed once on an import-order lint, then passed. Its last run gave 272 Python tests, clean pyright and arch-check, and 57 portal tests.
  - `make openapi` passed, and the portal now uses the generated journalist types.
  - `make infra-up`, `make migrate` and `make migrate-check` passed; all three database roles match the code.
  - `make seed` twice: the second run changed nothing, and `local.env` stayed the same.
  - `make test-integration`: 79 passed.
- **Step 8** (checking the database's per-tenant row security catches a missing tenant filter):
  - Run one, with the tenant filter taken out of the journalists list query and the table's policy on: 79 passed.
  - Run two, with the policy also off: 7 failed. The failures include the cross-tenant list case and the policy check, as intended.
  - Run three, with the filter and policy restored: 79 passed.
  - The record is in `docs/runbooks/tenant-isolation.md`.
- **Step 9** (run plus 3 reruns):
  1. `make devx-up` failed: Docker couldn't bind port 59090 for a moment.
  2. `make test-telemetry` failed because I hadn't set `ACME_OPS_HOME`. This run keeps the ops home in the tree at `.data/ops-home`, which is gitignored.
  3. It failed again because Prometheus had come up without its port after the first failure; I recreated that container.
  4. It failed because an unexpected 500 answered without `x-request-id`. I fixed that in `gateway/errors.py` and added a test that fails without the fix.
  5. It failed on the log line, as described above.
- `make traffic PROFILE=light DURATION=30` never ran.

**Decisions I made, since no one could answer questions:**
- **Journalist entity:** records who created and last changed it, and deletes are soft. It has no version check, so the last rename wins. There are no fields only the manager sets, and a desk may have several profiles. All of this is in decision record 0008.
- **Held-back portal versions:** react-router stays on 7 and @sentry/react on 10.
- **Portal features:** the portal leaves out the external sign-in button for now.

**Findings:** none. Step 10's review didn't run, so nothing was reviewed.

`HANDOFF.md` has the fix to make first and what's left. The local containers are still running; `make down` stops them.

The tree is uncommitted, and the first commit is the user's.

Stopped at step 9: make test-telemetry: the request log line did not arrive within 90s; a defect of this skill (the request-id middleware clears the id before it writes the log line, and the rerun count ran out)
