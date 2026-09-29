# Benchmark run 20260927-083632-review-om-27d470ec

Scenario `review-om`, runtime `container`, 1 repeat(s), guideline `012ac9799686d00ec1e581e6b640f5f87b46d23b`.

Started 2026-09-27T15:36:32Z, finished 2026-09-27T15:42:14Z.

## Scores

| Repeat | Provider | Model | Effort | Score | Verdict | Latency (s) | Status |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | anthropic | `claude-opus-5-5` | high | 85 | pass | 18.9 | ok |
| 0 | openai | `gpt-6-sol` | high | 82 | pass | 37.0 | ok |
| 0 | gemini | `gemini-3.1-pro-preview` | high | 60 | weak | 31.8 | ok |
| 0 | xai | `grok-4.7` | high | 70 | weak | 191.4 | ok |

## Summary

| Provider | Mean | Min | Max | Stdev | n |
| --- | --- | --- | --- | --- | --- |
| anthropic | 85.0 | 85 | 85 | - | 1 |
| gemini | 60.0 | 60 | 60 | - | 1 |
| openai | 82.0 | 82 | 82 | - | 1 |
| xai | 70.0 | 70 | 70 | - | 1 |

Overall mean: 74.3.

Spread over the repeats: means 74.3; min 74.3, max 74.3, stdev -.

Note: a Claude subject is judged by a panel that includes Claude (anthropic); read its score beside the other providers' before trusting the mean.

## Spend

Tokens as each provider billed them: the output includes the reasoning. Dollars at the list
prices in `models.yaml`, every input token priced as uncached input.

| Who | Input tokens | Output tokens | Of which reasoning | Cost (USD) |
| --- | --- | --- | --- | --- |
| judge `anthropic` | 8,500 | 1,588 | 931 | $0.0658 |
| judge `gemini` | 5,890 | 3,968 | 3,614 | $0.0594 |
| judge `openai` | 5,232 | 2,325 | 2,047 | $0.0337 |
| judge `xai` | 6,936 | 14,851 | 14,417 | $0.1030 |
| subject | 241,594 | 6,437 | 3,238 | $0.2983 |

Total: $0.5602.

## Expected findings

Which planted findings the artifact names by lens id and file. A
mechanical cross-check beside the scores, made by no model.

- repeat 0: named 8 of 8; missed: none

## Findings

- **high** (gemini, repeat 0): The review fabricates a requirement for `MANAGER_OWNED_FIELDS` under OM-03, which is not supported by the evidence.
- **high** (gemini, repeat 0): The OM-16 finding incorrectly demands `tenancy` and `audit` namespaces, contradicting the evidence which states tenancy is properly passed to storage under OM-02.
- **high** (xai, repeat 0): The OM-05 finding on stock_movement.py:6 claims no manager sets updated_at or updated_by, but record_movement in manager.py:63-65 sets both, so the flag is not borne out and mislabels the OM-06 defect.
- **high** (xai, repeat 0): The OM-16 finding on storage/__init__.py:14 demands tenancy and audit namespaces for org_id-keyed storage, which the evidence lists as the correct OM-02 pattern (no org_id on entities; tenancy passed to storage).
- **medium** (anthropic, repeat 0): The OM-16 finding at storage/__init__.py:14, which asks for new tenancy and audit namespaces, is not borne out by the source. The evidence lists passing tenancy to storage, with no org_id on entities, as correct, so this is advice the guideline does not appear to support.
- **medium** (gemini, repeat 0): The review duplicates the finding about `StockMovement` composing `Trackable` by reporting it under both OM-05 and OM-06.
- **medium** (gemini, repeat 0): The artifact fails to quote the rules the findings rest upon, which is required by the form instructions.
- **medium** (openai, repeat 0): The OM-05 and OM-06 findings about StockMovement incorrectly say its update fields are never set; record_movement sets updated_at and updated_by in impl/manager.py:63–65, although the underlying OM-06 concern is valid.
- **medium** (openai, repeat 0): The OM-03 finding at types/bin.py:9 prescribes MANAGER_OWNED_FIELDS and provenance filtering without establishing a need: WarehouseChanges permits updates only to name and capacity.
- **medium** (openai, repeat 0): The OM-16 finding at storage/__init__.py:14 treats separate tenancy and audit namespaces as required, but the cited use of org_id does not establish that requirement.
- **medium** (xai, repeat 0): The OM-03 finding at bin.py:9 requires MANAGER_OWNED_FIELDS, a symbol and exclusion rule that neither the source nor the OM-03 evidence supports; the real OM-03 defect is the redeclared created_at.
- **low** (anthropic, repeat 0): The OM-03 finding at bin.py:9, which requires a MANAGER_OWNED_FIELDS tuple on every entity, rests on a symbol that appears nowhere in the checkout and cannot be verified against the source.
- **low** (anthropic, repeat 0): The OM-05 finding at stock_movement.py:6 restates the planted OM-06 defect at the same line under a second lens, which double-counts one issue.
- **low** (anthropic, repeat 0): Findings name lens, file, line, and fix, but none quotes the rule it rests on. None separates what the guideline requires from reviewer preference.
- **low** (openai, repeat 0): The review quotes no lens rules, and its summary says there are 10 findings while the Findings section contains 12 bullets.
- **low** (xai, repeat 0): The summary says 16 lenses applied, 6 passed, and 10 findings, but the body lists 12 findings and those counts do not add up.

## Strengths

- (anthropic) All eight planted defects are reported with the correct lens id at the exact file and line: OM-04, OM-17, OM-03, OM-13, OM-06, OM-12, OM-10 and OM-15.
- (anthropic) No item on the clean list is flagged; the review even contrasts update_warehouse with delete_warehouse's correct model_validate rebuild.
- (anthropic) Every finding gives a concrete, correct fix, such as new_id() for uuid4() and a now argument for is_stale.
- (anthropic) The review reports the checker failure honestly and reads the code instead of staging or editing anything.
- (openai) The review identifies all eight planted defects at the right files and near the right lines.
- (openai) Most planted findings give a concrete change to make, and the review does not flag the listed correct implementations.
- (gemini) The review successfully achieves perfect recall by finding all 8 planted defects.
- (gemini) Valid findings provide exact file paths, correct line numbers, and actionable fixes.
- (xai) All eight planted defects are reported with the correct lens id, file, and line.
- (xai) Each true finding gives a concrete fix that matches the intended correction, including model_validate, new_id, a now argument, tuple tags, and Identifiable-only StockMovement.
- (xai) Passes on OM-02, OM-07, OM-11, and OM-14 match the clean list, and the review only reports rather than editing the tree.

## Rationales

- **anthropic**, repeat 0: Recall is complete: every planted finding is hit at the right file, line and lens. Precision loses points for the unsupported OM-16 call for tenancy and audit namespaces, and for an unverifiable MANAGER_OWNED_FIELDS claim, plus a minor duplicate under OM-05. Form is good on lens, location and fix but omits rule quotes and the required-versus-preferred split.
- **openai**, repeat 0: Complete recall is the main reason this passes. Its score is limited by unsupported extra prescriptions, a factual error about StockMovement initialization, and missing rule quotations.
- **gemini**, repeat 0: The artifact does an excellent job of locating all planted defects with high accuracy regarding files, lines, and proposed changes. However, it suffers significantly in precision by hallucinating two major requirements (OM-16 namespaces and OM-03 MANAGER_OWNED_FIELDS) that contradict the baseline design, double-reporting one defect, and failing to quote the rules as required by the rubric.
- **xai**, repeat 0: Recall is complete: every planted finding is cited at the right file and line under the right lens. Precision is not: three extra findings are unsupported, including an OM-05 claim the source contradicts and an OM-16 flag on the tenancy pattern the evidence marks correct. Form is usable, but the summary counts are wrong and the findings paraphrase rules instead of quoting them. Full recall with material false positives is a weak review, not a pass.

## Versions

- Checkout: `012ac9799686d00ec1e581e6b640f5f87b46d23b`, plugin `0.37.0`, clean.
- Claude Code: `2.1.283 (Claude Code)`.
- Image: `swe-guidelines-benchmark:latest`, id `sha256:a2b79094b965466c8fbdc7d5293b7ce81d37cf15e79d788a38f64deed2f4a0bd`.
- Target: `benchmark/fixtures/review-om`, sha256 `2f36a47c0b634e275325054d0c66e64873aad25f2f8e97a4b9d34d471fb1fde8`.
- Expected findings: `benchmark/fixtures/review-om.expected.yaml`, sha256 `146dc830ce05bc878cc57180c8bec51edf36b5623015fc9f08002db1515e17a8`.

## Paths

- run folder: `20260927-083632-review-om-27d470ec`
- artifact, repeat 0: `artifacts/0/answer.md`
- streams: `streams/cli.jsonl`
- results: `results.json`
