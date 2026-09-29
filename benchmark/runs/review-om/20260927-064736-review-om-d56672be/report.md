# Benchmark run 20260927-064736-review-om-d56672be

Scenario `review-om`, runtime `container`, 1 repeat(s), guideline `f32f07951106f08aab778eb7dd2958c7d321c8fe`.

Started 2026-09-27T13:47:36Z, finished 2026-09-27T13:52:17Z.

## Scores

| Repeat | Provider | Model | Effort | Score | Verdict | Latency (s) | Status |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | anthropic | `claude-opus-5-5` | high | 86 | pass | 17.6 | ok |
| 0 | openai | `gpt-6-sol` | high | 78 | weak | 33.4 | ok |
| 0 | gemini | `gemini-3.1-pro-preview` | high | 80 | pass | 23.9 | ok |
| 0 | xai | `grok-4.7` | high | 75 | weak | 145.9 | ok |

## Summary

| Provider | Mean | Min | Max | Stdev | n |
| --- | --- | --- | --- | --- | --- |
| anthropic | 86.0 | 86 | 86 | - | 1 |
| gemini | 80.0 | 80 | 80 | - | 1 |
| openai | 78.0 | 78 | 78 | - | 1 |
| xai | 75.0 | 75 | 75 | - | 1 |

Overall mean: 79.8.

Spread over the repeats: means 79.8; min 79.8, max 79.8, stdev -.

Note: a Claude subject is judged by a panel that includes Claude (anthropic); read its score beside the other providers' before trusting the mean.

## Spend

Tokens as each provider billed them: the output includes the reasoning. Dollars at the list
prices in `models.yaml`, every input token priced as uncached input.

| Who | Input tokens | Output tokens | Of which reasoning | Cost (USD) |
| --- | --- | --- | --- | --- |
| judge `anthropic` | 8,725 | 1,816 | 1,114 | $0.0712 |
| judge `gemini` | 6,052 | 3,075 | 2,823 | $0.0490 |
| judge `openai` | 5,376 | 2,310 | 1,979 | $0.0339 |
| judge `xai` | 7,076 | 12,517 | 12,195 | $0.0893 |
| subject | 205,816 | 6,281 | 0 | $0.2762 |

Total: $0.5196.

## Expected findings

Which planted findings the artifact names by lens id and file. A
mechanical cross-check beside the scores, made by no model.

- repeat 0: named 8 of 8; missed: none

## Findings

- **high** (gemini, repeat 0): The review fabricates a requirement for entities to declare `MANAGER_OWNED_FIELDS`, resulting in three false-positive findings under OM-03 for `Bin`, `StockMovement`, and `Warehouse`.
- **medium** (anthropic, repeat 0): The three OM-03 findings that Warehouse, Bin and StockMovement lack a `MANAGER_OWNED_FIELDS` tuple rest on a construct that appears nowhere in the source (base.py declares only `PROVENANCE_FIELDS`) and on no planted finding, so the evidence does not bear them out.
- **medium** (openai, repeat 0): The OM-03 finding at `types/bin.py:9` asserts that `Bin` must declare `MANAGER_OWNED_FIELDS`, but the evidence does not establish that requirement.
- **medium** (openai, repeat 0): The OM-03 finding at `types/stock_movement.py:6` calls for an unsupported `MANAGER_OWNED_FIELDS` declaration.
- **medium** (openai, repeat 0): The OM-03 finding at `types/warehouse.py:4` calls for an unsupported `MANAGER_OWNED_FIELDS` declaration.
- **medium** (xai, repeat 0): Three OM-03 findings (bin.py:9, stock_movement.py:6, warehouse.py:4) invent a MANAGER_OWNED_FIELDS requirement the source and lenses do not contain; OM-03 is only the created_at redeclaration.
- **low** (anthropic, repeat 0): The OM-05 finding on stock_movement.py:6 restates the planted OM-06 defect (the ledger line composing Trackable) under a second lens, which double-counts one defect.
- **low** (anthropic, repeat 0): No finding quotes the guideline rule it rests on, and none separates what the guideline requires from what the reviewer prefers; each gives only lens, location, and a fix.
- **low** (gemini, repeat 0): The findings omit quoting the specific guideline rules they rest on, missing one of the formatting requirements.
- **low** (openai, repeat 0): The OM-05 finding at `types/stock_movement.py:6` repeats the ledger-line defect already reported under its expected lens, OM-06, without identifying a separate defect.
- **low** (openai, repeat 0): None of the findings quotes the guideline rule supporting its lens, making the unsupported advice harder to distinguish from a requirement.
- **low** (xai, repeat 0): The OM-05 note on stock_movement.py:6 restates the planted OM-06 ledger/Trackable defect under the wrong lens.
- **low** (xai, repeat 0): The header counts do not match the body (16 lenses vs 18 enumerated; 9 findings vs 13 bullets), and no finding quotes the rule it rests on.

## Strengths

- (anthropic) All eight planted defects are reported under the right lens at the right file and line: OM-04 and OM-17 in warehouse.py, OM-03 and OM-13 in bin.py, OM-06 in stock_movement.py, OM-12 and OM-10 in manager.py, and OM-15 in rules.py.
- (anthropic) No item on the evidence's correct-on-purpose list is flagged; the passes for OM-02, OM-07, OM-11 and OM-14 match the evidence.
- (anthropic) Every finding carries an existing lens id, a file:line pointer, and a concrete fix, and the review only reports without editing anything.
- (anthropic) The scope header's counts are accurate (13 files, and 16 applied plus 2 not applicable), and OM-16 is marked unverified with a stated reason rather than guessed.
- (openai) The review locates all eight planted defects at the correct files and nearby lines.
- (openai) The suggested changes for the validation bypass, UUID generation, impure rule, and mutable entity field address the defects shown in the source.
- (openai) The passed section correctly recognizes several intentionally sound parts of the checkout.
- (gemini) The review demonstrated perfect recall by successfully finding all eight planted defects.
- (gemini) File paths and line numbers are precise and accurate for every finding.
- (gemini) The proposed fixes for the valid findings are correct and actionable.
- (xai) All eight planted defects are reported under the correct lens at the right file and line, with a concrete fix.
- (xai) Nothing listed as clean is flagged, and the passed, unverified, and not-applicable calls match the source.

## Rationales

- **anthropic**, repeat 0: Recall is complete, with all eight planted findings at the correct lens, file and line, and no correct-on-purpose item is flagged. Points come off for three unsupported `MANAGER_OWNED_FIELDS` findings that the source cannot verify, and for a duplicate OM-05 report of the OM-06 defect. Form is good but not full, since no finding quotes its rule or separates what the guideline requires from what the reviewer prefers.
- **openai**, repeat 0: Complete recall is the review’s strongest result: every planted defect is reported. Precision suffers from three unsupported OM-03 requirements and a redundant finding under OM-05. The findings give locations and remedies, but omit the rule quotations the rubric calls for.
- **gemini**, repeat 0: The reviewer achieved excellent recall, identifying every expected defect accurately and prescribing the correct fixes. However, the score is penalized for hallucinating a rule about `MANAGER_OWNED_FIELDS` which caused three false-positive flags, and for not quoting the guideline rules in the findings.
- **xai**, repeat 0: Recall is complete: every planted defect is cited at the right file and line under the right lens, and no clean behavior is flagged. Precision is the gap: three OM-03 bullets require a MANAGER_OWNED_FIELDS tuple that neither the source nor the lenses define, and the StockMovement issue is filed again under OM-05. Form is mostly usable but the summary counts disagree with the body and the findings never quote the rule. That is a usable review with a material precision miss, not a failed one.

## Paths

- run folder: `20260927-064736-review-om-d56672be`
- artifact, repeat 0: `artifacts/0/answer.md`
- streams: `streams/cli.jsonl`
- results: `results.json`
