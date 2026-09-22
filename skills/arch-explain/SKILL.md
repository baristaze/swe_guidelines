---
name: arch-explain
description: "Explain how the guideline applies to a question, a file, or a change, citing the sections and lenses that govern it. Use when onboarding or before a cross-layer change."
allowed-tools: Read, Grep, Glob
---

# arch-explain

Answer a question about the architecture with the guideline as the
source of truth, not with general opinion. The guideline is at
`${CLAUDE_SKILL_DIR}/../../architecture.md` and the lens catalog at
`${CLAUDE_SKILL_DIR}/../../lenses/`. If either is missing, stop and
say the installation is incomplete.

## Input

`$ARGUMENTS` is one of:

- a question ("where does rate limiting live?", "can a manager call a
  service?");
- a path to a file or folder in the current repository ("explain what
  rules apply to `om/orders/impl/manager.py`");
- a description of a change ("I want to add a nightly cleanup").

Empty arguments mean: give the guided tour, every section in the order
of the guideline's Contents, two sentences each, then the eight lens
groups in one line each.

## Procedure

1. Read the guideline's Contents (the table of contents under its
   first heading) and the lens group table in `lenses/README.md`.
2. Find the sections and subsections that govern the question. Read
   them in full; quote the `Principle` callouts verbatim when they
   answer the question directly.
3. Find the lenses that a reviewer would apply. Name them by id.
4. When the input is a path, open the code and say, for each rule that
   applies, whether the code follows it, in one line each. Do not run
   a full review; point at `arch-review-<group>`
   (`/swe-guidelines:arch-review-<group>` when installed as the plugin)
   for that.
5. When the input is a change, say which layer each part belongs to,
   which section shapes it, and which scaffold skill starts it.
6. When the guideline is silent on the question, say so and name the
   nearest section. Do not opine beyond the text.

## Output

Short, in prose, in the guideline's voice. Cite sections by title, as
`Section title, Subsection`, never by number, and lenses as `LENS-ID`. Do not restate the guideline at length;
quote the sentence that decides, then stop.
