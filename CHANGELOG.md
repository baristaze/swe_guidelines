# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.53.0 (2026-10-08)

A feature flag is an infra capability behind `FlagsInterface`, its
provider chosen at boot and apart from the identity provider, and a
client reads its session's flags as one snapshot from its own API.
Minor, with one reversal: DEL-22 no longer calls a wrapper around a
flag SDK a violation; a vendor's flag SDK outside the infra flags
package is one.

### Added

- `FlagsInterface` in infra, through `InfraInterface.get_flags()`.
  `evaluate(org_id, user_id)` answers every flag declared in code for
  one audience: a user's rule over an org's, over the provider's
  default, over the code's. A flag the provider does not know, or a
  provider that fails, reads its default.
- `ACME_FLAGS_BACKEND`: `memory` for tests and the local stack, which a
  deployed environment refuses; `launchdarkly`, through OpenFeature,
  refused without its key; or `none`, where every flag reads its
  default. The LaunchDarkly key is a process secret, and its runbook's
  go-live turns each flag's targeting on, serving the code's default.
  ADR 0085 records the decision.
- The scaffold's one flag, `media-uploads`: `create_file` refuses a new
  upload with `403 feature_off` where it is off.
- `GET /v1/flags`: the session's flags marked for clients, with an
  `ETag`, and `304` when nothing changed.
- The portal reads that snapshot once the tenant exchange is done, and
  again on focus and every five minutes; a switch drops it with the old
  tenant's caches. `useFlag` reads one flag, off until the snapshot
  arrives, and the Storage card says when new uploads are paused.
- The guideline's Infrastructure, Feature Flags, and Client App
  Architecture, Flags in the Client.
- DEL-52: a browser app depends on no flag vendor's SDK, which
  arch-check decides from each app's `package.json`.

### Changed

- DEL-22, reversed: product variation stays a modelled entity, and a
  release toggle or a kill switch is a flag behind `FlagsInterface`. A
  vendor's flag SDK or OpenFeature imported outside the infra flags
  package is the violation, which arch-check decides. DEL-20 no longer
  cites a flag SDK as one used directly.
- NET-20 allows a poll of what no push names, such as the flags
  snapshot.
- `arch-scaffold-app` says what a new browser app writes for flags.
