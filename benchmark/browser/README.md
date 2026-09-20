# The benchmark in the browser

The API harness one folder up asks a model through its SDK. This one
asks the same question through the product a reader would use:
chatgpt.com, claude.ai, and gemini.google.com, in a browser a person
has signed in to. It exists because that is the answer a reader gets
when they paste the prompt themselves, and because the products carry
tools (repository browsing, extended thinking) the raw API does not
hand out the same way.

It is semi-autonomous by design. The person signs in once, in the
browser the session drives. The session does the rest: picks the model
and the effort from two t-shirt sizes, checks the picker shows what the
sizes asked for before sending, sends the prompt and the contract,
waits, and saves the answer with its proof.

## Files

- `prompt.md`: the prompt as a reader would paste it, and the contract
  appended after it so the three answers line up.
- `sizes.yaml`: the two sizes (`xs`, `s`, `m`, `l`, `xl`) and what each
  site's picker calls them. When a site renames a model, this file
  changes; the skill does not.
- `../schema/browser-session.schema.json`: what a run writes.
- `../../skills/arch-benchmark-browser/SKILL.md`: the skill that runs
  it.

## What a run leaves behind

`~/Downloads/benchmark_browser/<YYYYMMDD-HHMMSS>/`:

```text
results.json          the run, in the schema: sizes, each session's URL, labels, times, score
chatgpt.com.md        the answer as the page showed it, under a header with the URL and the labels
claude.ai.md
gemini.google.com.md
```

The conversation URL is the proof: it carries the session id, and the
person can open it later and see the same answer. The labels are what
the picker showed at the moment the prompt was sent, read off the page
and not assumed from the size map.

## Words

A run is a benchmark, in full; the word is never shortened.
