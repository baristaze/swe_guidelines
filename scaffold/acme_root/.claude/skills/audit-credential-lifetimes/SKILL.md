---
name: audit-credential-lifetimes
description: "Audit how long each credential keeps working after it should stop: one row per credential kind (session, API key, operator token, sign-in, socket ticket, and any other the code has) and channel (HTTP request, realtime socket, bus control message, worker), with where it is checked, how often, what one check costs, the worst case a revoked credential keeps working, how a change of role or membership reaches it, its rate limit, and whether it fails open or closed when the cache is down. Flags every trust decision carried only by an at-most-once push, and every worst case above a stated bound. Never changes anything."
allowed-tools: Read, Grep, Glob, Write, Edit, Bash(uv run:*), Bash(git:*), Bash(mkdir:*)
---

# audit-credential-lifetimes

A credential is revoked, a member is removed, a role drops from admin to
member. How long until every place that trusted the old answer stops
trusting it? This audit answers that for every credential on every
channel it travels, from the code, and counts what each check costs on a
database of its own.

## Input

`[--bound <duration>]`

`--bound` is the longest a revoked credential, an ended membership, or a
lowered role may keep working anywhere: 5 minutes by default. Say the
bound and where it came from. The audit reads the checkout, tools and
code alike; to audit another commit, run it from a checkout of that
commit that has `ops/audit/`. Every command runs from the repository
root.

## Role and credential

None, local only. The counts run on the local stack (`make
infra-up`, with `make migrate` run once), in a database the run makes
and drops, with the identity provider's twin in place of the provider.
It holds no cloud credential, reads no environment, and reads no env
file.

## Procedure

1. Name the run: `audit_credential_lifetimes_<yyyymmdd>`, today's date
   in UTC. When `uv run python ops/audit/auditdb.py list` shows that
   name taken, another run holds it: add a suffix (`_2`), and never drop
   a database this run did not make. The evidence folder is
   `~/Downloads/acme_credential_lifetimes_<yyyy-mm-dd>/` and the report
   `~/Downloads/acme_credential_lifetimes_<yyyy-mm-dd>.md`; when either
   exists, both take the next free suffix (`_2`), so a run never
   overwrites another's; the database takes the same suffix. Make the folder (`mkdir -p`). Say which commit
   the run read (`git rev-parse HEAD`).
2. List the credential kinds. `CredentialKind` in
   `om/src/acme/om/context.py` names the ones the platform mints; the
   tenancy manager (`om/src/acme/om/tenancy/impl/manager.py`) mints and
   checks them, with each lifetime in its options (`TenancyOptions`).
   The values that apply are the settings
   (`services/api/src/acme/services/api/settings.py`) as the container
   passes them in (`services/api/src/acme/services/api/container.py`),
   not the options' own defaults, which can differ. Add every other
   proof the code trusts: the identity provider's webhook signature
   (read at `services/api/src/acme/services/api/gateway/webhooks.py`,
   checked in `integrations/src/acme/integrations/identity/deliveries.py`),
   and the identity provider's own session behind a sign-in (ended at
   sign-out, `_provider_logout` in the tenancy manager). Each goes in the
   row of the channel that uses it. A kind the
   code has and this list misses is a finding of its own.
3. List the channels, and which kinds reach each:
   - HTTP request: the gateway's dependencies in
     `services/api/src/acme/services/api/gateway/auth.py`
     (`current_context`, `current_identity`) and `gateway/admin.py` (the
     operator gate), which call `authenticate`, `authenticate_login`,
     and `admit_operator`.
   - Realtime socket: the ticket's redemption (`socket_principal` in
     `gateway/auth.py`, `redeem_ticket` in the manager), the handler's
     recheck and expiry (`services/api/src/acme/services/api/realtime/socket.py`),
     and the realtime service that ends sockets on a change
     (`services/api/src/acme/services/api/services/impl/realtime.py`,
     its `REVOCATIONS`).
   - Bus control message: every message on the topics bus
     (`infra/src/acme/infra/topics/`, `Topics`) whose arrival changes
     what a process trusts or does, such as a revocation that ends a
     socket. Read how it is published (the outbox relay,
     `om/src/acme/om/outbox/impl/relay.py`) and delivered (the Valkey
     impl, `topics/valkey.py`, and `topics/breaker.py`): say whether it is at
     most once, and what happens to a message sent while a subscriber
     is reconnecting.
   - Worker: a work item runs under a context the claim mints
     (`claim` in `om/src/acme/om/work/impl/manager.py`, through the
     tenancy manager's `service_context`), and a delivery from the
     identity provider is applied under its org's service context
     (`workers/maintenance/src/acme/workers/maintenance/deliveries.py`);
     read whether a handler (the deletions in `accounts.py` beside it)
     checks the actor's credential, membership, or role again when it
     runs, or acts on what the request knew when it enqueued.
   A kind that never travels a channel is `n/a` in that cell, with the
   reason in a word (a socket ticket on a worker).
4. Fill each cell from the code, each fact with its file:line:
   - where it is checked, and how often: every request, every recheck
     interval (the setting and its value), once at the handshake, never;
   - what one check reads: the storage methods it calls, and whether any
     answer is kept in a cache or in the process (and for how long);
   - the worst case a revoked credential keeps working: the longest path
     from the revocation's commit to the last use it allows. Name the
     parts: the bus (instant when delivered, and nothing when lost), a
     recheck interval, a cache's TTL, the credential's own expiry. A
     path that depends on a message the bus may drop takes the next
     bound that does not;
   - how a change of role, the member's teams, the membership's end, or
     the org's deletion reaches it: read again on the next check, pushed
     and closed, or never until expiry;
   - its rate limit or budget
     (`services/api/src/acme/services/api/gateway/ratelimit.py`, the
     settings), or none;
   - with the cache down (`infra/src/acme/infra/cache/breaker.py`):
     does the check fail open (allows) or closed (refuses), for the
     check itself and for its rate limit, and is that stated in the code
     as a choice.
5. Count what one check costs. Make the database and run the counter
   with the flows that reach the credentials:

   ```bash
   uv run python ops/audit/auditdb.py create audit_credential_lifetimes_<yyyymmdd>
   uv run python ops/audit/dbcalls.py run audit_credential_lifetimes_<yyyymmdd> --only sign_in,events \
     --out ~/Downloads/acme_credential_lifetimes_<yyyy-mm-dd>/calls.json \
     [--flows ~/Downloads/acme_credential_lifetimes_<yyyy-mm-dd>/more_flows.py]
   uv run python ops/audit/dbcalls.py summary ~/Downloads/acme_credential_lifetimes_<yyyy-mm-dd>/calls.json
   ```

   The built-in `sign_in` flow measures the session and the API key on
   `GET /v1/me` (area `baseline`) and the sign-in credential on
   `GET /v1/auth/memberships` (area `auth`); `events` measures the
   ticket's redemption and the socket's recheck (area `realtime`). The
   check's cost is the credential's own transactions in each call's
   `detail`, apart from what the route reads after it; tally
   `calls.json` with a scratch script (`uv run python <script>`), never
   a file in the repository. The counter runs the cache and the bus in
   memory, so no count includes a Valkey round trip: a check in the
   cache is read from the code. A credential they do not reach (the
   operator token on an operator route, whose mint takes an
   `Idempotency-Key`; a sign-in with its second factor; an API key warm)
   goes in a flows file of the run's own, written as
   `.claude/skills/audit-database-calls/references/flows.md` shows (read
   it before writing one). Use the warm round trips, and say so. A cost
   the counter cannot reach (a check made only in the cache) is read
   from the code and marked as read.
6. Drop the run's database, whatever happened before, and check it is
   gone:

   ```bash
   uv run python ops/audit/auditdb.py drop audit_credential_lifetimes_<yyyymmdd>
   uv run python ops/audit/auditdb.py list
   ```

7. Flag, and rank by the harm:
   - a trust decision carried only by an at-most-once push, with no
     periodic recheck and no expiry within the bound behind it: a lost
     message leaves it open until the credential expires;
   - a worst case above the bound, with its number;
   - a check that fails open on a path that decides access (a rate limit
     that fails open is a stated choice when the code says so, and goes
     under "Stated choices", not flagged);
   - a change of role that only a new credential reaches;
   - a check that costs more than its credential's baseline needs (a
     read repeated, a transaction per check that one would serve).
   Each finding gets its fix, its effort, and a proposed ticket. The
   skill files none unless the person asks for it in this session.
8. Write the report.

## What it never does

- Never writes to a shared database or to an environment: it logs in
  only to the database it made on the local stack, and drops it.
- Never modifies a tracked file, never commits, never opens a pull
  request: a run's own flows live in its evidence folder, and a fix is a
  proposal with its numbers.
- Never mints, prints, or reads a real credential, and never calls a
  real provider.
- Never gives a worst case it could not trace as bounded: an unknown is
  "not traced", with what would settle it.

## Output

`~/Downloads/acme_credential_lifetimes_<yyyy-mm-dd>.md`:

```markdown
# Acme: how long a credential keeps working

<commit>, counted on <database>, with the identity provider's twin. Bound: <duration> (<source>).

## The answer

<the longest worst case and where, every flag, most harmful first>

## Credentials by channel

| Credential | Channel | Checked at (file:line) | How often | One check (round trips, transactions) | Worst case after revocation | Role or membership change | Rate limit | Cache down | Verdict |
|---|---|---|---|---|---|---|---|---|---|

Verdicts: within bound, above bound, push only, not traced, n/a. Costs: measured or read.

## Findings, by harm

1. **<finding>.** Where (file:line). The worst case (numbers). The fix.
   Effort S/M/L. Proposed ticket: <title>.

## Stated choices

- <a fail-open or a missing recheck the code states as a choice, with file:line>

## What I could not trace
```
