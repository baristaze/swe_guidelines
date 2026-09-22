---
name: arch-new-aspect
description: "Incorporate a new aspect into the guideline and cascade it through the lenses, skills, docs, and README, naming the release level. Runs in a checkout of the guideline repository."
disable-model-invocation: true
allowed-tools: Read, Grep, Glob, Write, Edit, Bash(make check), Bash(make gen-skills), Bash(make gen-toc), Bash(git diff:*)
---

# arch-new-aspect

A guideline grows one aspect at a time, and each aspect lands in more
than one file: the guideline states it, a lens makes it checkable, a
skill applies it, the docs record it, and the release names it. This skill
takes a brief description of an aspect and does the whole landing, in
the guideline's voice, so the person supplies the idea and reviews the
result rather than chasing the cascade by hand.

## Where it runs

The current working directory must be a checkout of the guideline
repository: `architecture.md`, `lenses/README.md`, `AGENTS.md`, and a
`Makefile` with the `gen-toc`, `gen-skills`, and `check` targets all
present. Otherwise stop and say so. The copy under
`${CLAUDE_SKILL_DIR}/../..` is the installed plugin, not the checkout
being changed; read and edit the checkout's files, never the plugin's.

## Input

`$ARGUMENTS` describes the aspect in a sentence or a paragraph:
optionally a proposed title, a proposed place, and the questions it
should answer. Examples: "Why have we named specific technologies?
How does a project override one? Clarify and guide." or "Add a
'Reference Implementation' pointer at the end". Empty arguments: ask
for the aspect in one message and stop.

## Procedure

1. Read `AGENTS.md` end to end, the guideline's Contents, and the group
   table in `lenses/README.md`. Read the sections the aspect touches
   in full, and grep the guideline for the nouns the aspect uses, so
   every existing mention is known before anything is written.
2. Classify the aspect, and say which it is in the report:
   - a **rule**: states what we do; it earns a `> **Principle:**`
     callout and a lens;
   - a **clarification**: sharpens a rule that exists; the rule's text
     and its lens change in place, and no lens is added;
   - a **rationale**: why the guideline is the way it is (why
     technologies are named, why sections are unnumbered); it is prose,
     it earns a lens only for the part a reviewer can check;
   - a **pointer**: where to go next (a reference implementation, a
     companion document); it is a short section or a paragraph and
     earns no lens.
3. Decide the shape by answering these questions, in the report and
   before writing:
   - New section, new subsection, a paragraph inside an existing
     subsection, or one sentence at an existing mention? A rule with
     more than one principle is a section; one principle is a
     subsection; a qualification is a paragraph.
   - Where does it live? In the section whose layer it touches, in
     reading order. A rationale or a pointer goes at the end of the
     document, before the closing pointer section (the one whose title
     starts with `Next:`), which stays last. A pointer goes after any
     rationale already there. Which existing places should mention it, with a
     named anchor link, at the first place a reader needs it? A
     rationale or a pointer placed at the end is linked from the
     introduction, so a reader who does not reach the end still finds
     it.
   - Does the proposed title hold? Keep it unless it carries history
     or roadmap phrasing, punctuation that damages the anchor (a slash;
     a colon is fine), or repeats the document's subject; when it
     changes, list the proposed and the used title under Open.
   - Which principles does it state, and which lens group does each
     map to (`lenses/README.md` says what each group covers)? New lens
     or sharpened lens? A new lens takes the next id in its group and
     cites the new section by title.
   - Which vocabulary does it introduce? `make leaks` refuses product
     and hardware terms in the guideline and the lenses, and history
     terms in the guideline. An agent is an agent; say so. When the
     aspect needs a refused term, rephrase and say so in the report
     rather than widening the list.
   - What cascades? List each of these that applies:
     - The Contents, through `make gen-toc`.
     - The review skills, through `make gen-skills`, driven by
       `lenses/README.md`.
     - `checkers/src/arch_check/lenses.py`, for every lens added,
       removed, or re-rated: its id and its severity, equal to the
       lens file.
     - A new lens group. Its name and its id prefix go in `GROUPS` in
       `checkers/src/arch_check/model.py`. Its skill goes in the group
       list of `skills/arch-review-full/SKILL.md`, and its name in the
       group list of `agents/arch-reviewer.md`. Its lens file goes in
       the vendor loop of `docs/adopting.md`. Every count of the groups
       moves with it: the description, the procedure, the `Groups`
       line, and the By-group table of `arch-review-full`; the
       description of the agent; `arch-explain`; the id prefixes in
       `lenses/README.md`; and `README.md`. A group the checker decides
       in part gets its module under `checkers/src/arch_check/rules/`
       and its tests in `tests/test_arch_check_<group>.py`.
     - The scaffold skills whose output the aspect changes. That is
       any behavior the guideline changes: a route, an operation, a
       check, a test, a setting, or a step. It is not only a file a
       `Created` or `Changed` table gains, or a section a section
       list gains. Read every `arch-scaffold-*` skill for it, and
       grep them and `agents/` for every identifier the aspect
       renames.
     - `skills/_shared/`: `scaffold-conventions.md`, which every
       scaffold follows, and the ops templates under
       `skills/_shared/ops-skills/`, which a new tree copies as its
       operational skills. A rule that changes what a scaffold writes
       or what an operator runs lands there too.
     - `docs/adopting.md`, when an adopter must do something or gains
       a place to look.
     - `README.md`, when a count or a summary changes.
     - `AGENTS.md`, when a new invariant appears.
     - The release level. It goes in the report and never into
       `CHANGELOG.md`, since the release pull request writes the
       changelog. The report carries the release note the release
       pull request copies, in the style of the latest sections of
       `CHANGELOG.md`: one bullet under Added, Changed, Removed, or
       Fixed, naming the section title in quotes and the lens ids, then
       stating the change for a reader who adopts the guideline. It
       names no file and lists no cascade; the level is its own field.
       A reversal opens with `**Reversed.**`.
       A new or sharpened rule is minor. A removed or
       reversed rule is major; before 1.0.0 it bumps the minor number,
       and the entry names the reversal, as `CONTRIBUTING.md` states.
       A pointer, a rationale, or a wording change that states no new
       rule is patch.
4. Write the guideline text first, in its voice: present tense, no
   history and no rejected alternatives, one idea per paragraph,
   short sentences, wrapped at about 72 columns, cross-references as
   named anchor links, code snippets that show two entries and a
   `# ...` line where a pattern repeats. When a term the aspect uses
   is defined elsewhere in the document, link its first mention to
   that section.
5. Then the lenses, in the format `lenses/README.md` defines. Then
   `make gen-toc` and `make gen-skills`; record under Cascade whether
   any review skill was rewritten (a pointer or a rationale leaves all
   eight unchanged). Then the hand-written skills the aspect affects,
   the docs, and the README.
6. Search the repository for siblings of every change made
   (`git diff --stat` lists the files touched so far): a second
   snippet with the same pattern, a second place that mentions the
   same noun without the link, a second table that lists what the
   first one lists. Fix them in the same change.
7. Run `make check` and fix what it reports. Report a pre-existing
   failure and stop rather than editing unrelated files.

Never commit. Never edit a file outside the checkout. Never add a rule
the aspect does not state.

## Output

A short report, and nothing else:

```markdown
# New aspect: <title>

**Kind.** rule | clarification | rationale | pointer
**Shape.** <section | subsection | paragraph | sentence>, placed <where>
**Mentions.** <existing places that now link to it>
**Lenses.** <ids added or changed, with their groups>, or none, and why
**Cascade.** <files changed outside the guideline and the lenses>
**Release note.** <Added | Changed | Removed | Fixed>: <one bullet: the section title in quotes, the lens ids, and the change stated>
**Level.** <minor | major | patch>, and why
**Vocabulary.** <terms rephrased for the leak checker>, or none
**Text.** <the first sentence of the guideline text added; the reviewer reads the rest in `git diff`>
**Check.** `make check` <passed | failed: what>
**Open.** <questions the person should answer, or none>
```
