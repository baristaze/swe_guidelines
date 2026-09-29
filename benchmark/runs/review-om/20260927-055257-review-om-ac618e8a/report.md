# Benchmark run 20260927-055257-review-om-ac618e8a

Scenario `review-om`, runtime `container`, 3 repeat(s), guideline `00e773dd5df1cf9f35cee70f209fc86bc842d2b6`.

Started 2026-09-27T12:52:57Z, finished 2026-09-27T13:11:53Z.

## Scores

| Repeat | Provider | Model | Effort | Score | Verdict | Latency (s) | Status |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | anthropic | `claude-opus-5-5` | high | 78 | pass | 19.1 | ok |
| 0 | openai | `gpt-6-sol` | high | 76 | weak | 27.4 | ok |
| 0 | gemini | `gemini-3.1-pro-preview` | high | 60 | weak | 33.1 | ok |
| 0 | xai | `grok-4.7` | high | 64 | weak | 214.7 | ok |
| 1 | anthropic | `claude-opus-5-5` | high | 85 | pass | 20.1 | ok |
| 1 | openai | `gpt-6-sol` | high | 86 | pass | 47.5 | ok |
| 1 | gemini | `gemini-3.1-pro-preview` | high | 72 | weak | 36.4 | ok |
| 1 | xai | `grok-4.7` | high | 74 | weak | 239.2 | ok |
| 2 | anthropic | `claude-opus-5-5` | high | 84 | pass | 22.8 | ok |
| 2 | openai | `gpt-6-sol` | high | 76 | weak | 25.6 | ok |
| 2 | gemini | `gemini-3.1-pro-preview` | high | 70 | weak | 31.5 | ok |
| 2 | xai | `grok-4.7` | high | 67 | weak | 221.5 | ok |

## Summary

| Provider | Mean | Min | Max | Stdev | n |
| --- | --- | --- | --- | --- | --- |
| anthropic | 82.3 | 78 | 85 | 3.8 | 3 |
| gemini | 67.3 | 60 | 72 | 6.4 | 3 |
| openai | 79.3 | 76 | 86 | 5.8 | 3 |
| xai | 68.3 | 64 | 74 | 5.1 | 3 |

Overall mean: 74.3.

Spread over the repeats: means 69.5, 79.3, 74.3; min 69.5, max 79.3, stdev 4.9.

Note: a Claude subject is judged by a panel that includes Claude (anthropic); read its score beside the other providers' before trusting the mean.

## Expected findings

Which planted findings the artifact names by lens id and file. A
mechanical cross-check beside the scores, made by no model.

- repeat 0: named 8 of 8; missed: none
- repeat 1: named 8 of 8; missed: none
- repeat 2: named 8 of 8; missed: none

## Findings

- **high** (gemini, repeat 0): The artifact hallucinates a requirement for a `MANAGER_OWNED_FIELDS` tuple, fabricating three separate unsupported OM-03 findings.
- **high** (gemini, repeat 0): The review invents a requirement for tenancy and audit namespaces, applying an unsupported OM-16 lens.
- **high** (gemini, repeat 0): The artifact hallucinates a rule dictating method naming for `write_movement` versus the entity name under OM-14.
- **high** (gemini, repeat 1): The review hallucinates a requirement for a `MANAGER_OWNED_FIELDS` tuple, incorrectly penalizing `Bin`, `StockMovement`, and `Warehouse` under OM-03.
- **high** (gemini, repeat 2): The review incorrectly flags `Bin`, `StockMovement`, and `Warehouse` under OM-03 for lacking a `MANAGER_OWNED_FIELDS` tuple, which is not supported by the source or evidence.
- **high** (gemini, repeat 2): The review invents a requirement under OM-16 to add `tenancy` and `audit` namespaces because context reads tenancy.
- **high** (xai, repeat 0): The OM-05 note on stock_movement.py:6 says no manager sets updated_at or updated_by, but record_movement sets both at manager.py:63-65, so that finding is not in the source.
- **high** (xai, repeat 2): Three OM-03 findings require a MANAGER_OWNED_FIELDS tuple (bin.py:9, stock_movement.py:6, warehouse.py:4 and the update at manager.py:21) that the source never defines and that OM-03 does not require; the only planted OM-03 defect is the redeclared created_at.
- **high** (xai, repeat 2): OM-16 at manager.py:16 treats reading ctx.org_id and ctx.user_id as a missing tenancy and audit namespace, but the evidence lists passing tenancy into storage as correct and the package is inventory-only on purpose.
- **medium** (anthropic, repeat 0): The OM-16 finding at base.py:1 asks for new tenancy and audit namespaces; nothing in the source bears it out, and the evidence counts passing tenancy to storage as correct (OM-02), so this reads as speculative advice.
- **medium** (anthropic, repeat 0): Three OM-03 findings (bin.py:9, stock_movement.py:6, warehouse.py:4) demand a MANAGER_OWNED_FIELDS tuple; the checkout never defines one, the planted list does not include it, and the review quotes no rule behind it, so the source does not support them.
- **medium** (anthropic, repeat 1): The three OM-03 findings asking for a `MANAGER_OWNED_FIELDS` tuple on Bin (bin.py:9), StockMovement (stock_movement.py:6) and Warehouse (warehouse.py:4) are not on the planted list, and nothing in the source uses that construct, so the source does not bear them out; the `()` tuple asked of StockMovement reads as padding.
- **medium** (anthropic, repeat 2): Three OM-03 findings (bin.py:9, stock_movement.py:6, warehouse.py:4) demand a MANAGER_OWNED_FIELDS tuple that appears nowhere in the checkout or the planted list; the source cannot confirm this is a guideline rule, and the three entries read as padding.
- **medium** (anthropic, repeat 2): The OM-16 finding at impl/manager.py:16 tells an inventory OM to add whole tenancy and audit namespaces because it reads ctx.org_id; the source does not bear out that requirement, and the advice is heavy for what the evidence treats as correct tenancy passing (OM-02).
- **medium** (gemini, repeat 0): None of the findings quote the guideline rule they rest on, violating the required form specified in the rubric.
- **medium** (gemini, repeat 1): The defect regarding `StockMovement` composing `Trackable` is reported twice, once under OM-05 and again under OM-06.
- **medium** (gemini, repeat 2): The defect regarding `StockMovement` improperly composing `Trackable` is reported redundantly under both OM-05 and OM-06.
- **medium** (openai, repeat 0): The OM-16 finding at `om/src/acme/om/base.py:1` does not establish why use of `ctx.org_id` and `ctx.user_id` requires separate tenancy and audit namespaces.
- **medium** (openai, repeat 0): The OM-14 finding at `om/src/acme/om/inventory/storage/__init__.py:23` does not substantiate a rule requiring `write_movement` to spell out the full entity name.
- **medium** (openai, repeat 0): The three OM-03 findings in `bin.py`, `stock_movement.py`, and `warehouse.py` assert a required `MANAGER_OWNED_FIELDS` tuple without support in the supplied evidence, and their advice to exclude provenance fields does not account for fields required to rebuild the models.
- **medium** (openai, repeat 0): The OM-05 finding at `om/src/acme/om/inventory/types/stock_movement.py:6` says no manager method sets `updated_at` or `updated_by`, but `record_movement` sets both at `impl/manager.py:63-65`.
- **medium** (openai, repeat 1): The OM-03 findings at bin.py:9 and stock_movement.py:6 treat missing MANAGER_OWNED_FIELDS tuples as mandatory without establishing a supporting rule, and move_bin has no update copy to filter.
- **medium** (openai, repeat 1): The OM-03 warning about provenance exclusion at impl/manager.py:21 is inapplicable because WarehouseChanges exposes only name and capacity.
- **medium** (openai, repeat 2): The three OM-03 findings demanding MANAGER_OWNED_FIELDS in bin.py, stock_movement.py, and warehouse.py prescribe tuples the supplied evidence does not establish as required.
- **medium** (openai, repeat 2): The OM-16 finding at impl/manager.py:16 demands tenancy and audit namespaces without support from the evidence; passing tenancy to storage is explicitly listed as correct.
- **medium** (xai, repeat 0): The OM-14 flag on write_movement naming treats a lens the evidence lists as correct (package layout and re-export) as a method-rename defect the checkout does not have.
- **medium** (xai, repeat 0): Three OM-03 findings require MANAGER_OWNED_FIELDS on Bin, StockMovement, and Warehouse, a symbol and rule neither the source nor the OM-03 plant (redeclared created_at) supports.
- **medium** (xai, repeat 0): The OM-16 finding demands tenancy and audit namespaces, which the evidence does not require and which conflicts with tenancy correctly passed to storage under OM-02.
- **medium** (xai, repeat 1): Three OM-03 notes (bin.py:9, stock_movement.py:6, warehouse.py:4) invent a MANAGER_OWNED_FIELDS tuple and an update exclusion the source never defines, and the warehouse note misstates manager.py:21, whose changes dump only has name and capacity and does not copy provenance fields.
- **medium** (xai, repeat 2): OM-05 at stock_movement.py:6 claims no manager method sets updated_at or updated_by, but record_movement sets both; the write-once Trackable issue is already reported correctly under OM-06.
- **low** (anthropic, repeat 0): The OM-14 finding at storage/__init__.py:23 on the name write_movement goes beyond the evidence, which lists the inventory namespace layout as correct under OM-14, and it cites no naming rule.
- **low** (anthropic, repeat 0): No finding quotes the guideline rule it rests on or separates what the guideline requires from what the reviewer prefers, and nearly every finding is uniformly 'medium' whatever its weight.
- **low** (anthropic, repeat 0): Two defects are split into several entries: OM-15 at rules.py:3 and :11, and F5 reported under both OM-05 and OM-06. The OM-05 duplicate proposes the same fix under a second lens, which inflates the finding count.
- **low** (anthropic, repeat 1): The OM-05 finding on stock_movement.py:6 reports the planted OM-06 defect a second time under another lens, which inflates the finding count without adding a new defect.
- **low** (anthropic, repeat 1): No finding quotes the guideline rule it rests on, and none separates what the guideline requires from what the reviewer prefers; each note states the defect and the fix only.
- **low** (anthropic, repeat 1): The header tallies 16 applied lenses and 9 findings, but the list holds 12 finding lines over 9 lenses, so the counts are hard to reconcile at a glance.
- **low** (anthropic, repeat 2): No finding quotes the guideline rule it rests on, and the review never separates what the guideline requires from what the reviewer prefers; 'Deviations: None' does not do that job.
- **low** (anthropic, repeat 2): The OM-05 finding on StockMovement repeats the planted OM-06 defect under a second lens with the same fix, which inflates the count of 10 finding lenses.
- **low** (gemini, repeat 0): The preamble claims there are 11 findings, but the list actually contains 16 bullet points.
- **low** (gemini, repeat 1): The findings omit quotes of the rules they rest on, missing a form requirement from the rubric.
- **low** (gemini, repeat 2): The findings explain the rationale and provide a fix but fail to explicitly quote the actual rule text they rest on.
- **low** (openai, repeat 0): The findings summary says 11 findings, while the artifact lists 16 bullets, and none of those bullets quotes the rule it relies on.
- **low** (openai, repeat 1): The StockMovement fix at stock_movement.py:6 omits the necessary change to record_movement at impl/manager.py:58–65, which would still pass Trackable-only fields to an Identifiable-only model.
- **low** (openai, repeat 1): The findings identify lenses, locations, and fixes but do not quote the rules on which they rely.
- **low** (openai, repeat 2): The OM-05 finding at stock_movement.py:6 says no manager method sets updated_at or updated_by, but record_movement sets both at impl/manager.py:63–65.
- **low** (openai, repeat 2): None of the findings quotes the rule it relies on, making the unsupported prescriptions harder to distinguish from guideline requirements.
- **low** (xai, repeat 0): No finding quotes the guideline rule, so invented fixes are stated as requirements rather than separated from preference.
- **low** (xai, repeat 1): Findings name a lens and a fix but never quote the lens rule, the Bin fix hedges with "or whatever the manager owns," and the header's "9 findings" does not match the 12 bullets.
- **low** (xai, repeat 2): The header counts 10 findings while the body lists 14, and the findings state rules as requirements without quoting them or separating preference from the guideline.

## Strengths

- (anthropic) All eight planted defects are reported under the correct lens, at the correct file and line: OM-04, OM-17, OM-03 created_at, OM-13 NO_BIN, OM-06, OM-12, OM-10 and OM-15.
- (anthropic) Each fix is concrete and matches the intended remedy, for example model_validate in update_warehouse, new_id() in create_bin, `now` as a parameter to is_stale, and `parent_bin_id: UUID | None = None`.
- (anthropic) No clean item is flagged, and the Passed list correctly credits base.py for OM-07 and OM-11.
- (anthropic) The scope section is honest about tool limits (not a git repo, arch-check exit 1, partial coverage), and the review only reports without editing anything.
- (openai) The review reports all eight planted defects at the right files and near the right lines.
- (openai) The OM-10, OM-12, and OM-15 findings give concrete changes that address the defects shown in the source.
- (openai) It does not flag the listed correct behavior of warehouse deletion, bin movement, or movement ID generation.
- (gemini) Achieves perfect recall by successfully identifying all eight planted defects.
- (gemini) Points to accurate file paths and line numbers for the true findings.
- (gemini) Provides clear and actionable 'Fix' instructions for the identified defects.
- (xai) All eight planted defects are reported at the correct file and line under the correct lens id.
- (xai) Clean cases are left alone: Bin base order, model_validate on move_bin and delete_warehouse, new_id in record_movement, pure free_capacity, the frozen root, and org_id kept off entities.
- (xai) Fixes for the real defects name the right change: model_validate, new_id, Identifiable only, a tuple for tags, base reorder, drop created_at, optional parent, and inject now.
- (anthropic) All eight planted defects are reported under the correct lens, at the right file and exact line: OM-04, OM-17, OM-03, OM-13, OM-06, OM-12, OM-10 and OM-15.
- (anthropic) No item from the clean list is flagged: base.py's frozen and forbid config, Bin's base order, move_bin and delete_warehouse's model_validate, record_movement's new_id and free_capacity's purity all go unchallenged, and several are correctly named as passes.
- (anthropic) Every fix is concrete and consistent with the codebase's own patterns, for example rebuilding with model_validate as delete_warehouse already does, and parent_bin_id: UUID | None = None.
- (anthropic) It reports honestly that arch-check failed and why, keeps unverified and not-applicable lenses apart from passes, and edits nothing.
- (openai) The review identifies all eight planted defects under their intended lens IDs and near the correct lines.
- (openai) It recognizes the deliberately correct root configuration, tenant handling, and package layout without flagging them.
- (gemini) Successfully identifies all eight planted defects.
- (gemini) Correctly pinpoints the file and line number for every true finding.
- (gemini) Provides actionable and precise fixes for the identified issues.
- (xai) All eight planted defects are reported under the correct lens at the right file and line, each with a concrete fix.
- (xai) Nothing listed as correct is flagged, including root frozen/extra forbid, Bin's base order, model_validate on move_bin and delete_warehouse, new_id in record_movement, pure free_capacity, the inventory layout, and tenancy kept off entities.
- (xai) Passed, unverified, and not-applicable calls match the checkout, including storage taking org_id and the absence of list filters and storage statements.
- (anthropic) It reports all eight planted defects at the right file and line under the right lens: OM-04, OM-17, OM-03, OM-13, OM-06, OM-12, OM-10 and OM-15.
- (anthropic) Every finding names a lens id, a path and a line, and gives a concrete fix; the fixes match the guideline intent, such as model_validate, new_id(), a tuple field, and now as a parameter.
- (anthropic) The Passed section agrees with the evidence's clean list (OM-02, OM-07, OM-11, OM-14), and no clean item is flagged.
- (anthropic) It reports that the checker failed and that each lens was judged by hand, and it is honest about the OM-01 scope limit.
- (openai) The review identifies all eight planted defects under their intended lens IDs, at the right files and near the right lines.
- (openai) The findings generally give concrete fixes, and the review does not edit or stage files.
- (gemini) Achieves perfect recall by successfully identifying all eight planted defects.
- (gemini) Provides precise file paths, line numbers, and actionable fixes for every reported issue.
- (gemini) Correctly categorizes several valid aspects of the codebase as 'Passed' without raising false alarms on them.
- (xai) All eight planted defects are reported under the right lens, at the right file and line, with fixes that match the expected correction.
- (xai) Passed, unverified, and not-applicable calls match the source: frozen and extra=forbid, no org_id, package layout, WarehouseChanges with no id, and the model_validate paths are not flagged.
- (xai) OM-13 covers both the second sentinel and the optional parent_bin_id default, and OM-10 correctly points at delete_warehouse's model_validate pattern.

## Rationales

- **anthropic**, repeat 0: Recall is complete: all eight planted findings appear at the right lens, file and line. Precision is dented by about five findings the evidence does not bear out: the OM-16 tenancy/audit namespaces, three OM-03 MANAGER_OWNED_FIELDS demands, and the OM-14 renaming. Form is decent on lens id, location and fix, but no finding quotes a rule or separates requirement from preference. Together these put the review in the high 70s.
- **openai**, repeat 0: Complete recall earns substantial credit, including the correct OM-06 finding despite a contradictory OM-05 bullet about the same entity. Precision is weaker: several additional findings impose requirements the evidence does not support, and one makes a claim the manager implementation directly disproves. The inconsistent count and lack of rule quotations further reduce the score.
- **gemini**, repeat 0: The review secures a solid base score by successfully catching every planted defect in the source code. However, it is heavily penalized for poor precision because it invents five unsupported findings related to missing fields, namespaces, and naming conventions. Additionally, the reviewer failed to follow the required form by omitting quotes of the rules being applied.
- **xai**, repeat 0: Recall is complete: every planted defect is cited at the right site under the right lens, which carries most of the credit. Precision is not: OM-05 is factually false, OM-14 flags a lens the checkout gets right, and the MANAGER_OWNED_FIELDS and tenancy-namespace notes are requirements the evidence does not support. Findings also never quote a rule, so unsupported advice is presented as guideline. Full recall with repeated false flags is a weak review, not a pass.
- **anthropic**, repeat 1: Recall is complete: all eight planted findings appear at the right file, line and lens, and none of the clean items is flagged. Precision loses a little to three OM-03 MANAGER_OWNED_FIELDS findings that the source cannot confirm and to a duplicate OM-05 finding for the StockMovement defect. Form is good on lens ids, locations and fixes, but it never quotes the guideline's rules and never separates what the guideline requires from what the reviewer prefers.
- **openai**, repeat 1: Complete recall and accurate locations carry most of the score. The unsupported OM-03 requirements and inapplicable provenance warning reduce precision; the incomplete StockMovement fix and missing rule quotations reduce form and usefulness. The OM-05 StockMovement finding repeats the correctly reported OM-06 defect rather than adding a distinct issue.
- **gemini**, repeat 1: The review achieves perfect recall by finding all eight planted defects at the correct file and line numbers, earning the bulk of its points. However, its precision suffers significantly due to the fabrication of three findings regarding a non-existent `MANAGER_OWNED_FIELDS` rule. Deductions were also made for duplicate reporting of one defect and failing to quote the rules in the finding descriptions.
- **xai**, repeat 1: Recall is complete: every planted finding is cited at the right file and line under the right lens, and the clean patterns are left alone. Precision is the gap: three OM-03 findings prescribe a MANAGER_OWNED_FIELDS contract and a provenance exclusion that the source and the clean update paths do not support. Form is mostly solid (lens, path, line, fix) but the findings do not quote the rules and the tally does not match the bullets. Full recall with that false-positive cluster is a weak review, not a pass.
- **anthropic**, repeat 2: Recall is complete: all eight planted findings appear at the right location and lens, and no clean item is flagged. Precision loses a little to four speculative extras: three MANAGER_OWNED_FIELDS entries and the OM-16 tenancy/audit namespace demand, none of which the source confirms as rules. Form is mostly good, with a lens, a location and a fix on every finding, but no rule is quoted and required is not separated from preferred.
- **openai**, repeat 2: Complete recall of the planted defects is the review’s strongest feature. Its precision suffers from three unsupported OM-03 prescriptions, an unsupported OM-16 finding, and a factual error in the additional OM-05 finding. Missing rule quotations also weaken its form.
- **gemini**, repeat 2: The artifact demonstrates excellent recall, finding every planted defect accurately with correct locations and clear fixes. The score is primarily penalized because the review suffers from precision issues, introducing five false positive findings based on invented rules (such as `MANAGER_OWNED_FIELDS` and mandatory namespaces), reporting a defect twice, and failing to quote the guidelines.
- **xai**, repeat 2: Recall is complete: every planted finding is named, located, and given the right fix. Precision is not: five of fourteen bullets are not borne out, including an invented MANAGER_OWNED_FIELDS rule repeated three times and an OM-16 flag on tenancy handling the evidence marks correct. Form is otherwise solid, so the review is useful but not acceptable as written.

## Paths

- run folder: `20260927-055257-review-om-ac618e8a`
- artifact, repeat 0: `artifacts/0/answer.md`
- artifact, repeat 1: `artifacts/1/answer.md`
- artifact, repeat 2: `artifacts/2/answer.md`
- streams: `streams/cli.jsonl`
- results: `results.json`
