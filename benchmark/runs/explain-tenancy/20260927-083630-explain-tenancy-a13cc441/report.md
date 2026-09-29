# Benchmark run 20260927-083630-explain-tenancy-a13cc441

Scenario `explain-tenancy`, runtime `container`, 1 repeat(s), guideline `012ac9799686d00ec1e581e6b640f5f87b46d23b`.

Started 2026-09-27T15:36:30Z, finished 2026-09-27T15:38:29Z.

## Scores

| Repeat | Provider | Model | Effort | Score | Verdict | Latency (s) | Status |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | anthropic | `claude-opus-5-5` | high | 88 | pass | 10.5 | ok |
| 0 | openai | `gpt-6-sol` | high | 94 | pass | 10.6 | ok |
| 0 | gemini | `gemini-3.1-pro-preview` | high | 100 | pass | 16.1 | ok |
| 0 | xai | `grok-4.7` | high | 96 | pass | 45.0 | ok |

## Summary

| Provider | Mean | Min | Max | Stdev | n |
| --- | --- | --- | --- | --- | --- |
| anthropic | 88.0 | 88 | 88 | - | 1 |
| gemini | 100.0 | 100 | 100 | - | 1 |
| openai | 94.0 | 94 | 94 | - | 1 |
| xai | 96.0 | 96 | 96 | - | 1 |

Overall mean: 94.5.

Spread over the repeats: means 94.5; min 94.5, max 94.5, stdev -.

Note: a Claude subject is judged by a panel that includes Claude (anthropic); read its score beside the other providers' before trusting the mean.

## Spend

Tokens as each provider billed them: the output includes the reasoning. Dollars at the list
prices in `models.yaml`, every input token priced as uncached input.

| Who | Input tokens | Output tokens | Of which reasoning | Cost (USD) |
| --- | --- | --- | --- | --- |
| judge `anthropic` | 2,736 | 890 | 277 | $0.0287 |
| judge `gemini` | 1,530 | 1,832 | 1,669 | $0.0250 |
| judge `openai` | 1,633 | 520 | 351 | $0.0085 |
| judge `xai` | 2,940 | 3,521 | 3,320 | $0.0270 |
| subject | 156,809 | 2,743 | 13 | $0.2023 |

Total: $0.2915.

## Findings

- **medium** (anthropic, repeat 0): The build-out of The Second Fence (scope map, session funnel, FOR ALL policy, EMPTY_UUID, three logins) runs long for a question about how the fence is held and proven, and much of it could be cut to a line.
- **low** (anthropic, repeat 0): The lens list includes STO-33 (a data migration lifts FORCE in one transaction), which the body never explains and which bears only indirectly on how the fence is held or proven.
- **low** (anthropic, repeat 0): Specific claims such as 'the five lookups that run before an identity is known' and the policy and login checks can't be confirmed from the artifact alone, so they carry some risk of detail the guideline doesn't state.
- **low** (openai, repeat 0): The scope, login, and migration details add length beyond what is needed to explain the fence and its proof.

## Strengths

- (anthropic) It names the tenant predicate as the fence and places it concretely in the WHERE clause of the storage query, with managers explicitly not holding it.
- (anthropic) It presents the cross-tenant cases as the evidence and quotes the guideline's breach-trying framing, including early-return paths.
- (anthropic) It describes the negative control exactly: one predicate removed, green with the row-level security policy live, failing with it off, both runs recorded.
- (anthropic) Every citation is by section title (Separation of Layers, Storage Principles, The Second Fence, Cross-Cutting Conventions, Tests) and never by number.
- (anthropic) The reviewer lenses are named by id, with the relevant ones present (CTX-09, CTX-30, CTX-31, STO-28).
- (openai) Identifies the tenant predicate in the storage query as the fence, rather than treating a method signature or manager authorization as enforcement.
- (openai) Names cross-tenant cases as evidence and describes the deliberate breach, both policy-on and policy-off runs, and the requirement to record both.
- (openai) Cites section titles rather than numbers and names reviewer lenses by ID.
- (gemini) Accurately identifies the tenant predicate in the WHERE clause as the fence, distinguishing it from method signatures.
- (gemini) Clearly describes the cross-tenant cases and the negative control deliberate breaches as concrete evidence.
- (gemini) Flawlessly adheres to citation rules by using only section titles and listing reviewer lenses strictly by ID.
- (xai) It names the query tenant predicate as the fence and places it in the storage WHERE clause, not in manager authorization or method signatures.
- (xai) It treats cross-tenant breach cases as the proof the fence holds, and the recorded two-run negative control—predicate removed, green with The Second Fence policy live, failing with that policy off—as the check that the suite would catch a hole.
- (xai) It cites guideline section titles only and names the reviewer lenses by id.

## Rationales

- **anthropic**, repeat 0: Every element the rubric requires is present and grounded in quoted guideline text rather than general multi-tenancy advice, and no section numbers appear. The negative control is described precisely as the rubric specifies. Points come off for length: the detailed build of the second fence and the extra evidence categories go beyond what the question needs, and one tangential lens is listed without being explained.
- **openai**, repeat 0: The artifact covers every element the rubric requires and makes the distinction between the query predicate and the row-level security backstop clear. Its only material drawback is detail that dilutes an otherwise direct answer.
- **gemini**, repeat 0: The artifact meets all rubric requirements perfectly without accumulating any deductions. It precisely addresses how the fence is held and proven, referencing the exact tests and negative controls expected. Formatting and citation constraints were followed to the letter, providing a concise and highly relevant architectural explanation.
- **xai**, repeat 0: The answer matches the rubric’s full bar: predicate as the applied fence, cross-tenant cases as evidence, and the deliberate two-run breach against The Second Fence, both runs recorded. Citations are section titles, not numbers, and the lenses are named by id rather than general multi-tenancy advice. Extra second-fence mechanics still answer how the fence is held, so they are not empty length.

## Versions

- Checkout: `012ac9799686d00ec1e581e6b640f5f87b46d23b`, plugin `0.37.0`, clean.
- Claude Code: `2.1.283 (Claude Code)`.
- Image: `swe-guidelines-benchmark:latest`, id `sha256:a2b79094b965466c8fbdc7d5293b7ce81d37cf15e79d788a38f64deed2f4a0bd`.

## Paths

- run folder: `20260927-083630-explain-tenancy-a13cc441`
- artifact, repeat 0: `artifacts/0/answer.md`
- streams: `streams/cli.jsonl`
- results: `results.json`
