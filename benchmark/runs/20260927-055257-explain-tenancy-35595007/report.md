# Benchmark run 20260927-055257-explain-tenancy-35595007

Scenario `explain-tenancy`, runtime `container`, 3 repeat(s), guideline `00e773dd5df1cf9f35cee70f209fc86bc842d2b6`.

Started 2026-09-27T12:52:57Z, finished 2026-09-27T13:00:03Z.

## Scores

| Repeat | Provider | Model | Effort | Score | Verdict | Latency (s) | Status |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | anthropic | `claude-opus-5-5` | high | 85 | pass | 11.6 | ok |
| 0 | openai | `gpt-6-sol` | high | 95 | pass | 9.7 | ok |
| 0 | gemini | `gemini-3.1-pro-preview` | high | 100 | pass | 19.2 | ok |
| 0 | xai | `grok-4.7` | high | 96 | pass | 88.8 | ok |
| 1 | anthropic | `claude-opus-5-5` | high | 85 | pass | 10.2 | ok |
| 1 | openai | `gpt-6-sol` | high | 94 | pass | 7.7 | ok |
| 1 | gemini | `gemini-3.1-pro-preview` | high | 100 | pass | 13.8 | ok |
| 1 | xai | `grok-4.7` | high | 95 | pass | 56.9 | ok |
| 2 | anthropic | `claude-opus-5-5` | high | 88 | pass | 11.6 | ok |
| 2 | openai | `gpt-6-sol` | high | 98 | pass | 12.0 | ok |
| 2 | gemini | `gemini-3.1-pro-preview` | high | 100 | pass | 13.3 | ok |
| 2 | xai | `grok-4.7` | high | 95 | pass | 61.6 | ok |

## Summary

| Provider | Mean | Min | Max | Stdev | n |
| --- | --- | --- | --- | --- | --- |
| anthropic | 86.0 | 85 | 88 | 1.7 | 3 |
| gemini | 100.0 | 100 | 100 | 0.0 | 3 |
| openai | 95.7 | 94 | 98 | 2.1 | 3 |
| xai | 95.3 | 95 | 96 | 0.6 | 3 |

Overall mean: 94.3.

Spread over the repeats: means 94.0, 93.5, 95.3; min 93.5, max 95.3, stdev 0.9.

Note: a Claude subject is judged by a panel that includes Claude (anthropic); read its score beside the other providers' before trusting the mean.

## Findings

- **medium** (anthropic, repeat 0): The negative-control item (point 2) gives no section title for its source, although every other test kind is cited, so a reader cannot trace the deliberate-breach rule to its section.
- **medium** (anthropic, repeat 1): The Second Fence section spends many bullets on mechanics (set_config funnel, NULLIF, FOR ALL policy shape, three logins) that go well beyond what the question needs, adding length without sharpening the answer.
- **medium** (anthropic, repeat 2): The six-bullet breakdown of the row-level security machinery under 'The second fence' (scope map, funnel, NULLIF, EMPTY_UUID, three logins) is detail that does not answer 'how is the fence held and what proves it' and roughly doubles the length.
- **low** (anthropic, repeat 0): The detailed RLS mechanics under 'The second fence' (the scope values, the `set_config` funnel, `NULLIF`, the three logins) go beyond what the question needs and add length without strengthening the answer about what holds and proves the fence.
- **low** (anthropic, repeat 0): The login tests and policy-check test are presented as co-equal proofs under a 'four kinds of test' framing, which dilutes the rubric's point that cross-tenant cases are the evidence and the deliberate breach is the verification.
- **low** (anthropic, repeat 1): The framing 'The guideline asks for four pieces of evidence' may be the artifact's own tally rather than a count the guideline states, which risks reading as an invented rule.
- **low** (anthropic, repeat 1): Lens STO-33 (lifting FORCE during a data migration) is tangential to how the fence is held and proved, and pads the lens list.
- **low** (anthropic, repeat 2): Evidence items 3 and 4 (the policy check test and the connection assertions) go beyond the two proofs the question turns on, which dilutes the emphasis on the cross-tenant cases and the negative control.
- **low** (anthropic, repeat 2): The lens list glosses each id in a few words, but it does not say which lens covers the predicate-in-WHERE placement itself, so the fence and the lenses are only loosely linked.
- **low** (openai, repeat 0): The system-login and policy-catalog details add length beyond what is needed to answer how the tenant fence holds and how it is proved.
- **low** (openai, repeat 1): The policy implementation details and review-command advice make the artifact longer than needed to explain the fence and its proof.
- **low** (openai, repeat 2): The closing review-command sentence adds an action that does not help explain the fence or its proof.
- **low** (xai, repeat 1): The closing recommendation to run arch-review commands does not say how the fence is held or what proves it.
- **low** (xai, repeat 2): The second-fence inventory (set_config, EMPTY_UUID, login ownership) runs longer than the question needs once the predicate’s place and the recorded negative control are stated.

## Strengths

- (anthropic) It names the tenant predicate in the query's `WHERE` clause as the fence the business layer relies on, and says where it is applied rather than where it is hoped for.
- (anthropic) It identifies the cross-tenant contract cases as evidence and lists their required coverage over both memory and the engine.
- (anthropic) It describes the deliberate breach correctly: one predicate is removed, the suite runs twice, it is green with The Second Fence's policy live and failing with the policy off, and both runs are recorded.
- (anthropic) It cites sections by title only, with no section numbers, and names reviewer lenses by id.
- (openai) Identifies the query’s tenant predicate as the fence and locates it in the WHERE clause.
- (openai) Names cross-tenant contract cases as evidence and describes the deliberate breach, both suite runs, their expected outcomes, and the recorded results.
- (openai) Cites sections by title without numbers and names reviewer lenses by ID.
- (gemini) Clearly identifies the tenant predicate in the WHERE clause as the primary fence.
- (gemini) Accurately details the cross-tenant test cases and the two-run deliberate breach methodology exactly as requested.
- (gemini) Strictly adheres to citing by section title and avoids section numbers entirely.
- (gemini) Lists exact lens IDs without adding unnecessary filler text.
- (xai) Names the query tenant predicate as the fence the business layer relies on and places it in the WHERE clause rather than in a method signature.
- (xai) States the deliberate breach exactly: one query's tenant predicate removed, suite green with The Second Fence policy live and failing with that policy off, both runs recorded.
- (xai) Cites guideline section titles only and names reviewer lenses by id, including CTX-09, CTX-12, CTX-13, CTX-30, CTX-31, CTX-32, STO-28, and STO-30.
- (anthropic) It names the query's tenant predicate as the fence and places it in the WHERE clause, noting that a signature taking org_id only shows the tenant was offered, not applied.
- (anthropic) It names the cross-tenant cases from Tests as the fence's evidence and describes what they cover.
- (anthropic) It states the negative control exactly: one predicate is removed, the suite is green with the policy live and red with it off, and both runs are recorded.
- (anthropic) It cites sections only by title (Storage Principles, Namespace Shape, The Second Fence, Tests) and never by number.
- (anthropic) It lists reviewer lenses by id, including CTX-30 and CTX-31 for the cross-tenant case and the deliberate breach.
- (openai) It identifies the tenant predicate in the query’s WHERE clause as the fence, rather than treating an `org_id` parameter as sufficient.
- (openai) It names cross-tenant cases as evidence and describes the deliberate breach, both recorded runs, and their expected green and red results.
- (openai) It cites sections by title and names the reviewer lenses by ID.
- (gemini) Accurately names the tenant predicate as the primary fence and specifies it is applied in the WHERE clause.
- (gemini) Correctly identifies cross-tenant test cases and the deliberate breach (negative control) as the required evidence, noting the specific two suite runs.
- (gemini) Strictly adheres to citing the guideline by section title rather than number.
- (gemini) Clearly lists the appropriate reviewer lenses by their specific IDs.
- (xai) It names the query predicate as the fence and places it in the WHERE clause, not in a signature that only receives org_id.
- (xai) It states the negative control exactly: one predicate removed, the suite green with The Second Fence policy live and red with that policy off, both runs recorded.
- (xai) It cites the guideline by section title only and names the reviewer lenses by id.
- (anthropic) It names the tenant predicate as the fence and places it exactly: the `WHERE` clause of the query, cited to *The Storage Layer, Namespace Shape*, and it notes that a signature carrying `org_id` proves nothing.
- (anthropic) It gives the cross-tenant cases as the evidence, with the breach-attempt framing and coverage of early-return and raise paths, cited to *Cross-Cutting Conventions, Tests*.
- (anthropic) It states the negative control precisely: one predicate is removed, the suite runs green with the policy of The Second Fence on and red with it off, and both runs are recorded.
- (anthropic) It cites every section by title with no section numbers, grounds its claims in quoted text rather than generic multi-tenancy advice, and lists reviewer lenses by id.
- (openai) It identifies the query’s tenant predicate as the primary fence and locates it in the WHERE clause, with writes checked in storage.
- (openai) It names cross-tenant cases as evidence and describes the recorded negative control: the suite stays green with row-level security on and fails with it off.
- (openai) It cites section titles rather than numbers and names the reviewer lenses by id.
- (gemini) Accurately identifies the tenant predicate as the primary fence and specifies its concrete location in the WHERE clause.
- (gemini) Correctly describes the deliberate breach negative control test, detailing the two-run verification process with and without row-level security.
- (gemini) Strictly adheres to citation rules by referencing section titles exclusively and avoiding section numbers.
- (gemini) Explicitly and accurately lists the exact reviewer lens IDs as requested by the rubric.
- (xai) Names the tenant predicate in the query WHERE clause as the fence, and treats a signature that merely accepts org_id as not enforcement.
- (xai) States cross-tenant breach cases as the proof the fence holds, and the deliberate predicate removal with both policy-on green and policy-off red runs recorded.
- (xai) Cites guideline section titles rather than numbers and names the reviewer lenses by id.

## Rationales

- **anthropic**, repeat 0: Every element the rubric requires is present and accurately framed: the predicate as the fence, where it is applied, cross-tenant cases as evidence, and the two-run breach verification with both runs recorded. Citations use titles and the lenses are named by id. Points come off for the uncited negative-control claim and for RLS and login detail that lengthens the answer without adding to the core question.
- **openai**, repeat 0: The artifact meets every central requirement of the rubric, including distinguishing the query predicate from the row-level security backstop. The only meaningful deduction is for ancillary detail that makes the answer longer than necessary.
- **gemini**, repeat 0: The artifact perfectly fulfills all rubric requirements. It explicitly names the tenant predicate, cross-tenant test cases, and the deliberate breach verification process with exact expected outcomes. Citations rely exclusively on section titles and lens IDs, and the text remains highly focused without introducing generic advice or fluff.
- **xai**, repeat 0: The answer meets every element of a full response: the applied tenant predicate, cross-tenant cases as evidence, the recorded two-run breach against The Second Fence, title citations, and lens ids. It stays inside this guideline's terms rather than giving generic multi-tenancy advice, and it never cites a section number. Extra session and policy particulars run a little long, but they still answer how the second fence is held, so the score stays near the top.
- **anthropic**, repeat 1: Every element the rubric requires is present and anchored in quoted guideline text, with no section numbers and no general multi-tenancy advice. The deliberate breach is described exactly as the rubric specifies, including both runs recorded. Points come off because the mechanics of the second fence and some peripheral evidence and lenses add length beyond what the question needs. The 'four pieces of evidence' framing may also be the artifact's own structure rather than something the guideline states.
- **openai**, repeat 1: The artifact satisfies every substantive requirement of the rubric, including the distinction between the query predicate and row-level security as a backstop. The only meaningful deduction is for detail that adds little to the requested explanation.
- **gemini**, repeat 1: The artifact meets all the rubric's requirements perfectly. It clearly explains how the tenant fence is held using both predicates and policies, and correctly details the specific tests that prove it holds. The formatting rules regarding section citations and reviewer lenses were followed flawlessly, resulting in a comprehensive and highly professional response.
- **xai**, repeat 1: The answer meets the full standard: the tenant predicate is the fence, applied in the query rather than hoped for on a signature, with cross-tenant cases as evidence and the recorded two-run breach as proof the suite would catch a hole. It stays on what the guideline says, quotes section titles rather than numbers, and names lenses by id. The only deduction is a trailing command suggestion that does not answer the question.
- **anthropic**, repeat 2: Every element the rubric requires is present and correctly framed: the predicate as the fence, its location in the WHERE clause, the cross-tenant cases as evidence, and the two-run negative control with both runs recorded. Citations are by title, and the lenses are named by id. There is no generic advice and no invented rule is evident. Points come off mainly because the row-level security detail and the extra evidence categories add length beyond what the question needs.
- **openai**, repeat 2: The artifact meets the rubric’s substantive requirements and distinguishes the query predicate from the row-level security backstop. Its supporting detail is largely relevant; only the final instruction is unnecessary.
- **gemini**, repeat 2: The artifact follows all instructions perfectly, meeting every specific requirement of the rubric. It directly addresses the prompt by explaining the mechanisms of the tenant fence and the evidence that proves it, without introducing extraneous general advice or forbidden numbering. The thoroughness of the explanation adds direct value rather than unnecessary length.
- **xai**, repeat 2: The answer names the tenant predicate as the fence and locates it in the WHERE clause rather than in signatures or authorization. It names the cross-tenant cases and the recorded double run—green with The Second Fence policy live, failing with it off—as what proves the suite would catch a hole. It cites section titles only and lists lenses by id. A little of the row-level security mechanics runs past what the question needs, which keeps it short of a perfect score.

## Paths

- run folder: `20260927-055257-explain-tenancy-35595007`
- artifact, repeat 0: `artifacts/0/answer.md`
- artifact, repeat 1: `artifacts/1/answer.md`
- artifact, repeat 2: `artifacts/2/answer.md`
- streams: `streams/cli.jsonl`
- results: `results.json`
