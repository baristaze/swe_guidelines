# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.52.2 (2026-10-07)

`arch-benchmark-browser` stops a session whenever its page offers to
act on the person's machine, and a copy's access-line test no longer
fails on a slow answer. Patch: a skill's steps and a test, two runs
recorded, and nothing is reversed.

### Fixed

- `arch-benchmark-browser` looks for a line that names a device of the
  person's, in any state, right after the send, at every poll, and
  once a site is done. Whenever it shows, the session is stopped with
  the product's stop control, recorded `not-run`, and scored on
  nothing. A link to download an app is not such a line, and no
  evidence picture shows a device line.
- The same skill reads an answer once, when its site is done; records a
  decline in the model's own words as `refused`, never `errored`;
  starts a site's polls a minute apart, the first a minute after the
  send; and gives a row the letter of the earliest matching set when
  more than one matches. Three passages moved unchanged to its
  `references/`.
- The scaffold's `test_the_access_line_times_the_answer_not_the_relay`
  holds the access line's `duration_ms` to the time from the send to
  the relay's measured start, not to a fixed 300 ms: the relay always
  starts after the line's end mark, and a collector pause in the answer
  could pass the old bound.

### Added

- Two runs of the browser benchmark on 2026-10-07, at sizes `m, m`:
  on `3cd6665`, chatgpt.com 94 and grok.com 81; on `54bc842`,
  chatgpt.com 92 and grok.com 84. gemini.google.com declined both,
  unable to reach the repository, and claude.ai was stopped in both.
