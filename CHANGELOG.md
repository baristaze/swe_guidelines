# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.40.0 (2026-09-29)

The scaffold departs from no `core` section, and every loop its ops
skills run has a count again. Five choices the scaffold recorded as
departures from `core` sections are now the rules they departed from,
and How to Read This says how far a tag reaches. Minor: rules are
sharpened, and two reversals are named below.

### Changed

- How to Read This: a tag covers the text under its heading up to the
  next heading of any level, so a `##` tag never reaches the `###`
  sections under it.
- OM-12 and STO-06: a record whose id is derived from a key that names
  it, an outside delivery's key or an orchestration step's record and
  row, takes `derived_id()`, so a second run makes the same id. Every
  other id is `new_id()`.
- Reversed: OM-16 and OM-02 no longer ask for an `audit` namespace from
  the start. An audit entry is an `Event` with an audit kind in the
  org's stream until audit gains a reader of its own or must be kept
  longer than the stream.
- NET-22 and DEL-18: the event stream keeps a retention and a floor,
  and a read below the floor is `410 stream_truncated`, naming the
  floor and the head. An exception that no listed shape fits sets its
  own status and code under the root.
- STO-34: payloads and events, an audit entry among them, carry ids and
  never a personal value, so an erasure has nothing to redact in the
  stream, and the trim past the retention is no breach.
- STO-32: a person's account may be deleted at once, in one write,
  beside the soft delete purged after its retention.
- Reversed: CTX-27's recheck setting is `realtime_recheck_seconds`, a
  setting of the realtime service, not `session_recheck_interval`.
- The scaffold's tables: the company site's HTML and CSS is a
  substitution under Stack, a `default` section, named in ADR 0002. The
  record of the event's `produced_at` leaves the deviations, since
  OM-06 asks for it. The five folded records stay as decisions.

### Fixed

- The scaffold's ops skills bound their loops again, as 0.38.0's
  templates did before 0.39.0 replaced them. `ops-watch` runs at most
  30 batches of 30 seconds to five minutes, with at most 20 tool calls
  a batch. `ops-root-cause` follows at most 5 request ids, one pass
  each, and reads the events feed from the window's first `seq`. A Logs
  Insights query is polled at most 10 times. `audit-database-calls`
  reruns a failed flow at most once. A session follows at most 2 hops
  of Next, and two skills leave their Next to the person.
- `ops-watch` reads each metric minute once: a batch rounds its bounds
  down to whole minutes, with a 60-second period, where the templates
  counted a minute in two batches.
- `ops-root-cause` asks for the symptom when neither the prompt nor an
  investigation's report names one and no request id is given.
- `make skills` holds each count bound in the skill that states it,
  beside the fix-and-rerun wording it held before.
