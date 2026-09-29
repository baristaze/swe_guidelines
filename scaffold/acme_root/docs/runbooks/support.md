# Support: one tenant names a problem

How a support investigation runs, from a tenant's message to a finding.
The supporter holds a `read` operator token and changes nothing.

## The steps

1. **Take the problem.** "Changes stopped reaching our screens at ten",
   "Bob cannot sign in". Ask for the org's slug or id, the person, the
   time, and the request id the client showed with any refusal.
2. **Take the org id.** From the slug, through the operator plane's read
   of the orgs. Nothing else is looked up by name.
3. **Run `ops-root-cause`** with the environment, the org id, and the
   request id when there is one. It checks its identity, refuses a wider
   credential, and reaches the plane with the token in
   `~/.config/acme/ops/<env>.env`. When the plane refuses the token, the
   supporter writes a fresh one in their own terminal:

   ```bash
   uv run acme-ops token --env <env> --identity operator
   ```

   It reads, in this order:
   - **The platform's size** (`acme-ops size`): one org's problem, or
     everyone's.
   - **The alarms and the dashboard** for the window.
   - **The request id through every signal** (`acme-ops signals check`):
     the log lines, the metric, the trace, and the error event.
   - **The org's records on the plane**: the org, its members, and its
     event stream around the time named. The stream is numbered without
     gaps, so a change that never reached a screen is either in the
     stream and not pushed, or not in it and never made.
   - **The errors** for the window, filtered on the environment
     (`environment:<env>`), since one tracker project holds them all.
4. **Write the finding**: the cause in one line, its evidence, and whose
   fix it is: the platform's (a pull request) or the tenant's (a refusal
   that was right).

## What is logged

- The org id, the time window, and the request ids read.
- The skill's report as it printed it, with the identity it checked.
- Nothing personal beyond those ids. An address or a name read on the
  plane is not copied into the log.

## What the supporter never does

- Sign in as the tenant, or connect to the database.
- Change a record. A fix is a pull request, or the tenant's own action.
- Widen the credential. What the read cannot see is escalated with the
  finding so far.

## When it fails

- **The skill stops on its identity check.** The profile or the token is
  not the read one. Sign in again under the investigate profile, and
  write a fresh `read` token.
- **The stream starts after the time named.** Its oldest events are
  trimmed past the retention, 90 days by default
  ([ADR 0040](../adr/0040-the-event-stream-has-a-floor.md)). The org's
  rows still show the state as it is.
