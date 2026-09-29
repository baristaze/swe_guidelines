# Support: one tenant names a problem

How a support investigation runs, from the message to the finding.
The supporter holds a read credential and changes nothing.

## The shape

1. **A tenant names a problem.** "Our tasks stopped updating at ten",
   "Bob cannot sign in", "the list shows a task we deleted". Ask for
   the org's slug or id, the person, the time, and, when the client
   showed one, the request id every refusal quotes.
2. **The supporter takes the org id.** From the slug, through the
   operator plane's read of the org. Nothing else is looked up by
   name.
3. **Run `ops-root-cause`** with the environment, the org id, and the
   request id when there is one. It verifies its identity, refuses a
   wider credential, and reaches the operator plane with the `read`
   operator token in the env file, never a password; when the plane
   refuses it, the supporter mints a fresh one in their own terminal
   (`uv run acme-ops token --env <env> --identity operator`). It reads
   in this order:
   - **The platform's size** (`acme-ops size`): whether the problem
     is one org's or everyone's, before anything else.
   - **The alarms and the dashboard** for the window: the 5xx ratio,
     the p95, the queue depth and age, the worker outcomes.
   - **The request id through every signal** (`acme-ops signals
     check`): the log lines, the metric that moved, the trace, and
     the error event.
   - **The org's records on the operator plane**: the org, its
     members, its tasks, and its diary around the time named. The
     diary is numbered without gaps, so a change that did not reach a
     screen is either in the diary and not pushed, or not in the diary
     and never made.
   - **The errors** for the window, by service and request id, out of
     the product's one tracker project filtered on this environment
     (`environment:<env>`): the project holds every environment's
     errors, so a read that leaves the filter out answers with
     another environment's.
4. **The finding** names the cause in one line, the evidence for it,
   what changed, and whether it is the platform's to fix (a pull
   request) or the tenant's (a refused write that was right to
   refuse).

## What is logged

- The case: the org id, the time window, and the request ids read.
- The skill's report, as it printed it, with the identity it verified.
- Nothing personal beyond the org id and the request ids. A person's
  email or display name is read on the operator plane when the case
  needs it and is not copied into the log.

## What the supporter never does

- Never signs in as the tenant. The operator plane's read answers what
  the case needs.
- Never connects to the database. The role denies it.
- Never changes a record. A fix is a pull request or a tenant's own
  action, told to them with what to do.
- Never widens the credential to see more. What the read role cannot
  see is escalated with the finding so far.

The roles, the profiles, the skills, and the signals are described in
[ops/README.md](../../ops/README.md).
