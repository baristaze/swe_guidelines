# Contributing

Thank you for taking the time. A change that sharpens a rule, fixes an
inconsistency, or improves a skill is welcome. A change that adds a rule
needs a reason the guideline can state in its own voice.

## Ground rules

- The guideline states what we do, in the present tense, with no
  history. It names its technologies on purpose, and never the
  vocabulary of one product (`make leaks`).
- The guideline tells the story. A lens or a skill holds the checkable
  detail under a rule it states, stricter on purpose and never
  contrary. [`AGENTS.md`](AGENTS.md) holds the writing rule and the
  layout.
- The review skills are generated. Edit `skills/_template/` or the
  lenses, and run `make gen-skills`.

## Workflow

1. Open an issue first for anything beyond a typo, so the change is
   discussed as a rule before it is discussed as a diff.
2. Fork, branch from `main`, and make the change.
3. Run `make check`. It needs Python 3.10 or newer, Node 24, and `uv`,
   and fetches the rest at pinned versions.
4. Open a pull request. Say which rule changes and why, and whether the
   change is major, minor, or a patch. Leave `CHANGELOG.md` alone.

## Versioning

Releases are tagged `vMAJOR.MINOR.PATCH`. A change that removes or
reverses a rule is major. A change that adds or sharpens a rule is
minor. Everything else is a patch. Before 1.0.0, a removed or reversed
rule bumps the minor number, as semver reads 0.x, and the changelog
names the reversal.

The changelog is written once per release, never per change, since a
pull request that edits it conflicts with every other open one. The
release pull request reads the squash commits since the last tag, picks
the highest level among them, and writes the release section of
`CHANGELOG.md`, grouped as Fixed, Added, Changed, and Removed, with
every reversal named as one. That section replaces the one before it:
the changelog holds the latest release, and each release's notes stay
on its GitHub release. It moves the version in
`.claude-plugin/plugin.json`, and `make version` holds every copy to
it. The tag goes on the squash of that pull request, and
`.github/workflows/release-tag.yml` fails a tag that names another
version.

`.github/workflows/release.yml`, dispatched on `main` with that squash,
publishes the release once a reviewer of the `human-approval`
environment approves. It fast-forwards the `release` branch to the
squash, tags it, and publishes the GitHub release with the changelog's
section as its notes. It refuses while that environment has no required
reviewer, so nothing is published unreviewed.

## License

By contributing you agree that your contribution is licensed under the
MIT License in `LICENSE`.
