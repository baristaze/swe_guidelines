# Benchmark run 20260927-064736-explain-tenancy-cb44b4aa

Scenario `explain-tenancy`, runtime `container`, 1 repeat(s), guideline `f32f07951106f08aab778eb7dd2958c7d321c8fe`.

Started 2026-09-27T13:47:36Z, finished 2026-09-27T13:49:20Z.

## Scores

| Repeat | Provider | Model | Effort | Score | Verdict | Latency (s) | Status |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | anthropic | `claude-opus-5-5` | high | 85 | pass | 10.2 | ok |
| 0 | openai | `gpt-6-sol` | high | 93 | pass | 12.2 | ok |
| 0 | gemini | `gemini-3.1-pro-preview` | high | 100 | pass | 10.0 | ok |
| 0 | xai | `grok-4.7` | high | 96 | pass | 38.9 | ok |

## Summary

| Provider | Mean | Min | Max | Stdev | n |
| --- | --- | --- | --- | --- | --- |
| anthropic | 85.0 | 85 | 85 | - | 1 |
| gemini | 100.0 | 100 | 100 | - | 1 |
| openai | 93.0 | 93 | 93 | - | 1 |
| xai | 96.0 | 96 | 96 | - | 1 |

Overall mean: 93.5.

Spread over the repeats: means 93.5; min 93.5, max 93.5, stdev -.

Note: a Claude subject is judged by a panel that includes Claude (anthropic); read its score beside the other providers' before trusting the mean.

## Spend

Tokens as each provider billed them: the output includes the reasoning. Dollars at the list
prices in `models.yaml`, every input token priced as uncached input.

| Who | Input tokens | Output tokens | Of which reasoning | Cost (USD) |
| --- | --- | --- | --- | --- |
| judge `anthropic` | 2,673 | 841 | 263 | $0.0275 |
| judge `gemini` | 1,480 | 1,105 | 916 | $0.0162 |
| judge `openai` | 1,589 | 571 | 407 | $0.0089 |
| judge `xai` | 2,907 | 3,147 | 2,933 | $0.0247 |
| subject | 178,873 | 2,861 | 0 | $0.2296 |

Total: $0.3069.

## Findings

- **low** (anthropic, repeat 0): The four-part breakdown of The Second Fence (declared scope, funnel, NULLIF, three logins) goes past what the question needs and makes the answer longer than it has to be.
- **low** (anthropic, repeat 0): The policy check and login assertions are listed as coequal proofs next to the cross-tenant case and negative control, which blurs the rubric's split between evidence (cross-tenant cases) and verification (the deliberate breach).
- **low** (anthropic, repeat 0): Specific claims such as the 'five pre-identity lookups' and the STO-33 parenthetical are stated precisely but add detail a reader cannot easily check against the question's scope.
- **low** (openai, repeat 0): The detailed login and session mechanics and closing review commands add length beyond what the tenant-fence question needs.

## Strengths

- (anthropic) It names the tenant predicate as the fence and places it concretely in the WHERE clause of storage queries, quoting Namespace Shape and Separation of Layers.
- (anthropic) It presents the cross-tenant contract case as the evidence and lists the paths it must cover.
- (anthropic) The negative control is described exactly: one predicate removed, green with the policy live, failing with it off, both runs recorded, with the 'claim, not evidence' quote.
- (anthropic) Every citation uses a section title and none uses a number.
- (anthropic) Reviewer lenses are named by id and paired with the matching review skills.
- (openai) The answer locates the primary fence in each storage query's tenant predicate rather than treating the method signature as enforcement.
- (openai) It identifies cross-tenant contract cases as evidence and describes the recorded negative control: green with row-level security on and failing with it off.
- (openai) It cites section titles rather than numbers and names reviewer lenses by ID.
- (gemini) Accurately identifies the tenant predicate in the WHERE clause as the primary fence.
- (gemini) Correctly details the cross-tenant tests and the deliberate breach (negative control) methodology as the evidence that the fence holds.
- (gemini) Strictly adheres to citing guidelines by section title without using any numbers.
- (gemini) Explicitly lists the appropriate reviewer lenses by ID as requested.
- (xai) Names the tenant predicate as the fence and places it in the query WHERE clause, where storage applies it, rather than in a signature or a manager hope.
- (xai) States the proof as cross-tenant contract cases plus the deliberate breach: one predicate removed, suite green with The Second Fence policy live, failing with that policy off, both runs recorded.
- (xai) Cites only section titles and names reviewer lenses by id (CTX-09, CTX-12, CTX-13, CTX-30, CTX-31, CTX-32, STO-28, STO-33).

## Rationales

- **anthropic**, repeat 0: Every element the rubric requires is present and accurate: predicate as fence and where it is applied, cross-tenant cases as evidence, and the two-run negative control against The Second Fence with both runs recorded. Citations use titles and lenses are named by id, with no general multi-tenancy advice. Points come off because the RLS mechanics and the extra proof categories add length beyond what the question needs and slightly dilute the evidence-versus-verification framing.
- **openai**, repeat 0: The artifact meets the rubric's substantive requirements, including the distinction between the query predicate and the policy that catches a missing predicate. Its only meaningful weakness is excess detail around the core explanation.
- **gemini**, repeat 0: The artifact perfectly fulfills every requirement of the rubric for a full answer. It correctly names the fence, details its application, outlines the exact testing and verification strategies required, and rigorously follows the formatting constraints for citations and lens IDs. The detail provided is specific to the guideline and highly relevant, avoiding any generalized advice or empty padding.
- **xai**, repeat 0: The answer does what the rubric requires: the predicate is the fence, applied in the query, proved by cross-tenant cases and by the recorded two-run breach against The Second Fence. It stays inside the guideline, cites titles rather than numbers, and names lenses by id. The extra RLS and login detail answers how the fence is held rather than padding it.

## Paths

- run folder: `20260927-064736-explain-tenancy-cb44b4aa`
- artifact, repeat 0: `artifacts/0/answer.md`
- streams: `streams/cli.jsonl`
- results: `results.json`
