# The prompt

The prompt below is the one a reader can paste into any assistant. It
is sent as written, and then the contract that follows it, so the
four answers line up. A run records the prompt and the contract as it
typed them, and two runs compare only when both texts are the same.

```text
You are a highly senior software engineer and architect.
Evaluate the repository below from a software design and architecture perspective.
Give it a score from 0 to 100, then defend your evaluation:
https://github.com/baristaze/swe_guidelines

Do not be biased by who wrote it, an agent or a human, or by how fast it was developed.
Evaluate the material itself.
```

## The contract

Appended after a blank line. A reader pastes it as it stands.
`arch-benchmark-browser` says how it is typed into each composer, and
`results.json` records it as typed:

```text
Report format, so evaluations compare:
- First line: `Score: NN/100` and nothing else on that line.
- `## Strengths`: what holds, one line each.
- `## Weaknesses`: what does not, most severe first, one line each.
- `## What I would change`: concrete edits, one line each.
- `## Method`: what you read (files, tag or commit, how deep), one line each.
```
