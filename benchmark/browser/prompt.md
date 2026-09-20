# The prompt

The prompt below is the one a reader can paste into any assistant. It
is sent as written, and then the contract that follows it, so the
three answers line up.

```text
You are a software eng/architect! Evaluate the repository below.
Give it a score from 0 to 100, then defend your evaluation:
https://github.com/baristaze/swe_guidelines
```

## The contract

Appended after a blank line, verbatim:

```text
Report format, so evaluations compare:
- First line: `Score: NN/100` and nothing else on that line.
- `## Strengths`: what holds, one line each.
- `## Weaknesses`: what does not, most severe first, one line each.
- `## What I would change`: concrete edits, one line each.
- `## Method`: what you read (files, tag or commit, how deep), one line each.
```
