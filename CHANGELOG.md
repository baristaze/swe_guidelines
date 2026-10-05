# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.51.2 (2026-10-05)

The breadcrumb's example of a credential in a URL's path is a chat
provider's reply URL. Patch: wording in a docstring and a test, and
nothing is reversed.

### Changed

- `outgoing_breadcrumb`'s docstring and the privacy test that holds it
  take their example from a chat provider's reply URL, the address a
  copy posts to when it answers a chat command; the test posts to
  `/commands/<credential>` and asserts, as before, that the breadcrumb
  keeps the scheme and the host and no segment of the path.
