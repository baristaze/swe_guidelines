# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.48.0 (2026-10-02)

A repository whose own scaffold builds on this one, a layer, takes the
scaffold unchanged and merges it, and a copy moves from the source its
base records. Minor: a tool and a skill gain a mode, and nothing is
reversed.

A layer keeps the scaffold in `scaffold/acme_root/`, and products are
copied from it. One tool and one skill then move every layer and every
product, from one tarball, a private source included.

### Added

- `scaffold/base.py --layer` commits the source's `scaffold/` folder
  unchanged, at `scaffold/`, onto a layer's `scaffold` branch: one
  commit per render, its parent the render before, with the same
  trailers. A layer's render records the name `acme`, which no copy
  takes, so `base.py` never moves a copy's base as a layer's, or a
  layer's as a copy's.
- `base.py` reads a private source. On a 404 from codeload, it asks
  GitHub's API with the token `gh auth token` gives, in a header no
  redirect carries. Without such a token, it refuses and names
  `--tarball`, with the `gh api` call that writes one.
- `arch-upgrade-scaffold` moves a layer, its first take included. It
  passes `--layer`, reads the pin, the ADRs, and the migrations under
  `scaffold/acme_root/`, and runs the gates there; it takes `--source`
  and `--tarball`. The adopting page says what a layer is.

### Changed

- A move without `--source` takes the source its base records, and a
  `--source` that names another is refused: a base keeps one source.
- `new.py`, run from a layer's checkout, records the checkout's
  `origin` on GitHub as the source, and keeps the guideline release its
  scaffold pins rather than the layer's own manifest version.

### Fixed

- `arch-upgrade-scaffold` reads the release tags in version order
  (`--sort=v:refname`), so `v0.10.0` follows `v0.9.0`.
