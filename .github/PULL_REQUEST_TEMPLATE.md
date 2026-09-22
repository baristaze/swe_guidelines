## What changes

One paragraph, in the guideline's voice, naming the rule, lens, or
skill that changes and why.

## Checklist

- [ ] `make check` passes locally.
- [ ] A rule change updates `architecture.md`, its lens, and (via
      `make gen-skills`) the generated skills together.
- [ ] The description names the level (major, minor, or patch) and a
      reversal as one. `CHANGELOG.md` is not edited; the release pull
      request writes it.
- [ ] No product or hardware vocabulary entered any Markdown
      `make leaks` scans: the guideline, the lenses, the skills, the
      agents, the docs, the checkers, the benchmark, the `.github/`
      templates, and the root guides.
