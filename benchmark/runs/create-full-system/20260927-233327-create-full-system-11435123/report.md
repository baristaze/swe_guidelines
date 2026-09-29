# Benchmark run 20260927-233327-create-full-system-11435123

Scenario `create-full-system`, runtime `vm`, 1 repeat(s), guideline `3009fa9d25b987f0e553daa83bc521a68c4aa42b`.

Started 2026-09-28T06:33:28Z, finished 2026-09-28T09:16:22Z.

Optional groups taken: `extras`.

## Scores

| Repeat | Provider | Model | Effort | `guideline` | `reference` | Weighted | Tool calls | Latency (s) | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

## Summary

| Provider | Mean | Min | Max | Stdev | n |
| --- | --- | --- | --- | --- | --- |

Overall mean, of the weighted scores: 0.0.

### References

| Reference | Weight | Mean | Min | Max | n | Gaps high / medium / low |
| --- | --- | --- | --- | --- | --- | --- |
| `guideline` | 0.4 | - | - | - | 0 | 0 / 0 / 0 |
| `reference` | 0.6 | - | - | - | 0 | 0 / 0 / 0 |

The harness weighs each judgement's scores: 0.4 * `guideline` + 0.6 * `reference`.

Spread over the repeats: means 0.0; min 0.0, max 0.0, stdev -.

Failed repeat(s) 0: the subject failed, and each scores 0 in the means.

Repeat 0 ended after phase scaffold, which ended with Agent calls that had no result. mvp, review, close did not run. It is not judged, and it scores 0 as a failed repeat.

## Spend

Tokens as each provider billed them: the output includes the reasoning. Dollars at the list
prices in `models.yaml`, every input token priced as uncached input.

| Who | Input tokens | Output tokens | Of which reasoning | Cost (USD) |
| --- | --- | --- | --- | --- |
| subject | 176,141,283 | 592,170 | 101,447 | $82.5281 |

Total: $82.5281.

## Phases

Each session of the subject: how it ended, the bound that ended it, its turns, its wall time, what it spent,
and each model it used with that model's cost, as Claude Code's result reports them.

| Repeat | Phase | Session | Status | Cap | Turns | Wall (s) | Cost (USD) | Models | Checkpoint |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | scaffold | fresh | incomplete | - | 500 | 9739.4 | $82.5281 | `claude-opus-5-5` $82.5281 | `2fc0ead5a450` |

## Output

The output's last commit, archived whole; `results.json` has each zip's SHA-256.

- repeat 0: `artifacts/0/output.zip`, 533 file(s), 760,400 bytes; manifest `artifacts/0/MANIFEST.txt`

## Gates

The scenario's gates, run on the final tree. They are recorded beside the scores and cap none.

- repeat 0: `make check` passed
- repeat 0: `make test-integration` passed

## Gaps

### `guideline` (weight 0.4)

No judge named a gap.

### `reference` (weight 0.6)

No judge named a gap.

## Strengths

No judge named a strength.

## Rationales


## Versions

- Checkout: `3009fa9d25b987f0e553daa83bc521a68c4aa42b`, plugin `0.37.0`, clean.
- Claude Code: `2.1.283 (Claude Code)`.
- Target: `benchmark/fixtures/create-full-system`, sha256 `97bc0dbcd93b5725cdd3030a1b1b5978315ee26ec14725c1f3c641d478933f13`.
- Reference `guideline`: `architecture.md`, `lenses`, `skills` of the checkout, sha256 `fad652d1331bdfdf1c895e07531cc9f4f63fbaff729c50a979af4bb124a36c2a`.
- Reference `reference`: https://github.com/baristaze/tadas at tag `v0.7.0`, commit `82e4d050ef53d5f17139f0f90476eaf157b6ff08`, pins the guideline at `v0.37.0`.

## Paths

- run folder: `20260927-233327-create-full-system-11435123`
- artifact, repeat 0: `artifacts/0/answer.md`
- artifact, repeat 0: `artifacts/0/output.zip`
- artifact, repeat 0: `artifacts/0/MANIFEST.txt`
- streams: `streams/cli.jsonl`
- results: `results.json`

## Notes

- repeat 0: phase scaffold ended with Agent calls that had no result: toolu_018U2A14ffHqV66yknoQNJuJ (Write Terraform, workflows, cloud scripts), toolu_01M52aLTaFR8L3v31ABf5Zx2 (Write ops/audit database tools), toolu_01VCtXxc8TG5sMY9Xz29TPR3 (Build the React portal app); mvp, review, close did not run, and the repeat is not judged
- subject failed in repeat(s) 0; not judged
