# Architecture

This project follows the Software Design and Architecture Guidelines:
<https://github.com/baristaze/swe_guidelines/blob/v0.38.0/architecture.md>
(pinned at release `v0.38.0`; the pin moves one release at a time, in a
pull request of its own).

The guideline is the source of truth for how this system is shaped, and
the tree is the system as built. `docs/adr/` records the decisions that
constrain future work, every deliberate deviation among them.

## Substitutions

None. Acme adopts every technology the guideline names
([ADR 0002](../docs/adr/0002-technology-choices.md)).

## Deviations

| ADR | Rule | Summary |
|-----|------|---------|
| [0010](../docs/adr/0010-operators-work-through-the-api.md) | DEL-16, The Operator Console | No console app: operators work through `/v1/admin/*` with `acme-ops` and an operator token, until an operator task needs a screen. |
| [0011](../docs/adr/0011-audit-entries-are-events.md) | OM-16, Namespaces as Swimlanes | An audit entry is an `Event` with an audit kind in the org's stream; an `audit` namespace comes when audit has a reader of its own. |
| [0015](../docs/adr/0015-the-event-keeps-its-produced-at.md) | OM-06, Naming Entities | `Event` keeps `produced_at`: the wire record carries no id, and the trim and the size count read the column through its index. |
| [0019](../docs/adr/0019-an-api-key-hash-stays-unique-after-revocation.md) | STO-26, Defining ORM Classes | `uq_api_keys_key_hash` stays a full unique index, so the lookup can answer "revoked". |
| [0020](../docs/adr/0020-the-cli-signs-in-as-a-person.md) | DEL-17, The CLI Is Different | The CLI signs in as a person and holds one session in one org; an API key in `ACME_TOKEN` works as the rule describes. |
| [0021](../docs/adr/0021-each-environment-has-an-aws-account-of-its-own.md) | OPS-18, Cost Boundaries | While the organization's Cost Explorer is off, the create script leaves the anomaly monitor out, and the budget stands alone. |
| [0024](../docs/adr/0024-what-staging-hands-production-is-recorded-and-verified.md) | DEL-31, DEL-38, Cloud: AWS | The kept builds carry no object lock: only replication writes them, and the recorded digest refuses a changed copy. The release push is a deploy key's, not the host's app. |
| [0027](../docs/adr/0027-what-a-delivery-creates-takes-an-id-derived-from-it.md) | OM-12, Identifiers | What an outside delivery creates takes a v7 id derived from the delivery's key, so a copy handled again creates nothing. |
| [0030](../docs/adr/0030-the-company-site-is-html-and-css-not-react.md) | DEL-12, Client App Architecture, Stack | The company site is HTML and CSS on Vite, with no script and no React. |
| [0040](../docs/adr/0040-the-event-stream-has-a-floor.md) | NET-22, Realtime at the Edge; DEL-18, Exceptions | The stream is gapless above a per-org floor the trim moves; a read below it is `410 stream_truncated`, a status the shapes do not list. |
| [0041](../docs/adr/0041-an-account-is-deleted-at-once-and-its-providers-by-the-queue.md) | STO-32, Database Roles | An account is a hard delete in one commit, outside the sweep. |
| [0053](../docs/adr/0053-the-scope-rides-in-the-message-that-begins.md) | STO-28, The Second Fence | The scope goes out in the message that begins the transaction, as checked literals, not as a bound statement of its own. |
| [0058](../docs/adr/0058-a-socket-asks-again-and-its-pong-answers-from-the-bus.md) | CTX-27, Stages | The recheck interval is `realtime_recheck_seconds`, beside the realtime service's other bounds, not `session_recheck_interval`. |
| [0080](../docs/adr/0080-the-optional-audits-count-as-well-as-read.md) | OPS-11, Operational Skills | The two optional audits also count, through the twin, on a database of their own. |
| [0081](../docs/adr/0081-the-desired-count-is-the-roots.md) | OPS-17, Scale-Out as a Lever | `desired_count` is not in `ignore_changes`: an apply returns a scaled service to its floor. |
