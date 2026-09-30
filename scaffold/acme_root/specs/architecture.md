# Architecture

This project follows the Software Design and Architecture Guidelines:
<https://github.com/baristaze/swe_guidelines/blob/v0.41.0/architecture.md>
(pinned at release `v0.41.0`; the pin moves one release at a time, in a
pull request of its own).

The guideline is the source of truth for how this system is shaped, and
the tree is the system as built. `docs/adr/` records the decisions that
constrain future work, every deliberate deviation among them.

## Substitutions

Recorded in [ADR 0002](../docs/adr/0002-technology-choices.md). Acme
adopts every other technology the guideline names.

| ADR | Rule | Summary |
|-----|------|---------|
| [0002](../docs/adr/0002-technology-choices.md) | DEL-12, Client App Architecture, Stack | The company site is HTML and CSS on Vite, with no script and no React. |

## Deviations

| ADR | Rule | Summary |
|-----|------|---------|
| [0010](../docs/adr/0010-operators-work-through-the-api.md) | DEL-16, The Operator Console | No console app: operators work through `/v1/admin/*` with `acme-ops` and an operator token, until an operator task needs a screen. |
| [0019](../docs/adr/0019-an-api-key-hash-stays-unique-after-revocation.md) | STO-26, Defining ORM Classes | `uq_api_keys_key_hash` stays a full unique index, so the lookup can answer "revoked". |
| [0020](../docs/adr/0020-the-cli-signs-in-as-a-person.md) | DEL-17, The CLI Is Different | The CLI signs in as a person and holds one session in one org; an API key in `ACME_TOKEN` works as the rule describes. |
| [0021](../docs/adr/0021-each-environment-has-an-aws-account-of-its-own.md) | OPS-18, Cost Boundaries | While the organization's Cost Explorer is off, the create script leaves the anomaly monitor out, and the budget stands alone. |
| [0024](../docs/adr/0024-what-staging-hands-production-is-recorded-and-verified.md) | DEL-31, DEL-38, Cloud: AWS | The kept builds carry no object lock: only replication writes them, and the recorded digest refuses a changed copy. The release push is a deploy key's, not the host's app. |
| [0053](../docs/adr/0053-the-scope-rides-in-the-message-that-begins.md) | STO-28, The Second Fence | The scope goes out in the message that begins the transaction, as checked literals, not as a bound statement of its own. |
| [0080](../docs/adr/0080-the-optional-audits-count-as-well-as-read.md) | OPS-11, Operational Skills | The two optional audits also count, through the twin, on a database of their own. |
| [0081](../docs/adr/0081-the-desired-count-is-the-roots.md) | OPS-17, Scale-Out as a Lever | `desired_count` is not in `ignore_changes`: an apply returns a scaled service to its floor. |
