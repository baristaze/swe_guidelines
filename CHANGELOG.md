# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.31.0 (2026-09-22)

Agents and pipelines reach the operator plane with a short-lived
operator token, never a password. A data migration passes the fence it
runs under, an update is held to the version its caller read, and an
upload is bounded in size. The guideline, the lenses, and the
scaffolds agree where they still differed. `arch-check` reads the
spellings real code uses, and the benchmark's subject holds no judge
key. Minor: rules are added and sharpened, two are reversed, and three
lenses change severity, which before 1.0.0 bumps the minor number.

### Changed

- **Reversed.** "Infrastructure, Buckets", lens `ASY-08`: an upload is
  a presigned POST. `presign_post` returns a `PresignedPost`, the URL
  and its form fields, whose policy carries the content type and a
  `content-length-range` up to `max_bytes`. A presigned PUT cannot
  bound the size, so `presign_put` is removed. The local impl holds
  the same bound and refuses a key with `..`.
- **Reversed.** "Deployment, Cloud: AWS", lenses `DEL-50`, `DEL-38`:
  the fast rollback goes to the previous release only, no longer to
  any earlier release production ran. It swaps the image digests and
  the portal bundle, runs no migration, and plans no Terraform.
  Anything older rolls forward through a revert. `DEL-38` names
  `DEL-50`.
- Severities follow the lens catalog. `CTX-13` moves from medium to
  high, like `CTX-14`: platform data under a real tenant's id is a
  tenancy breach. `NET-17` and `NET-22` move from high to medium: a
  lost frame or a gap costs a replay, and storage still holds every
  write.
- "The Second Fence", lens `CTX-12`: the lookups that run before an
  identity is known are five; `read_identity_by_issuer_subject` joins
  the four. The section lists every system-scope method by name,
  `purge_items` among the tenant-less sweeps. An identity-scoped table
  composes `IdentityScopedMixin`: `id` and `identity_id`, no `org_id`.
- "Shape of an Operation", lenses `STO-22`, `CON-17`, `CON-19`,
  `OM-03`, `CTX-35`: the expected version comes from the caller, as
  `If-Match` or `expected_version`, never one re-read inside the
  update. A mismatch is `PreconditionFailed`, a 412, and a PATCH with
  neither is refused with `ValidationFailed`. Each entity declares
  `MANAGER_OWNED_FIELDS` beside `PROVENANCE_FIELDS`, and the update
  copy excludes both; `credential_ref` and `version` are
  manager-owned. `CON-17` owns the update stamping, and `OM-05` names
  it.
- "Topics" and "Maintenance Without a Scheduler", lenses `ASY-10`,
  `ASY-19`: an effect that must happen rides an outbox row or a work
  item, never a topic alone. The sweep purges done and failed work
  items past retention with `purge_items(before)`.
- "The Gateway", lens `NET-28`: an inbound `x-request-id` is accepted
  only when it parses as a UUID; otherwise the gateway mints one.
- "Intra-Service Communication" and "Secrets", lenses `NET-27`,
  `ASY-13`: the internal signing key is a process credential injected
  at start, never read through the tenant capability.
- "Auth: the Gateway Verifies, the Tenancy Domain Owns", lens
  `CTX-36`: failed sign-ins are delayed per email digest, and an
  attempt inside the delay is refused 429 before the password is
  checked. A session has an idle and an absolute lifetime, 24 hours
  and 7 days by default.
- One lens owns each breach. `NET-12` owns the public address, and
  `DEL-48` names it. An audit entry's `org_id` is a storage column,
  so `NET-22` and `OM-02` agree it is no model field. `DEL-31` keeps
  every version under a locked bundle prefix, and production reads
  the one whose hash matches the record.
- The rest of the guideline: after a restore, the runbook clears
  `done_at` on outbox rows newer than the restored role's point; a
  staging destroy applies the commit of staging's last successful
  deploy; a deployer's trust expects the subject the repository host
  issues, the immutable one built from the ids; the security defaults
  name the `release` ruleset and its one bypass actor; the plaintext
  risk inside the network, sign-up's email verification, the
  connection budget, a rotated password, what still needs egress, the
  permissions boundary, the database engine's major version, feature
  flags, and Object Lock are each settled where the rule lives.
  Duplicated passages are kept once, and the Next section is one
  sentence.
- The scaffolds follow: operator tokens in place of passwords and TOTP
  secrets, minted by `POST /v1/admin/me/tokens` or the grant job;
  identity-scoped tables; the fifth pre-identity lookup; the caller's
  version and 412; `MANAGER_OWNED_FIELDS`; the data migration's shape
  and its two-tenant test; `purge_items` and `item_retention`; with
  `--no-worker`, the sweep in the API's lifespan; a UUID-only request
  id; the signing key injected at start; the `release` ruleset and
  environment; `rediss://` to the cache when deployed; cloud names in
  the root's kebab-case slug.
- The ops templates keep secrets out of the model's context: the env
  file holds operator tokens, and a command sources it and makes the
  call in one step. `ops-watch` polls bounded intervals. Create and
  nuke, `arch-benchmark`, and `arch-benchmark-browser` run only when
  invoked by name, and the nuke stops after its dry run for a
  person's go.
- The review scope reads a range, a commit, and `all` before a path,
  includes untracked files, and names its fallback when there is no
  merge base. A ref's files are read with `git show`, and `arch-check`
  runs only on a working-tree scope.
- `arch-new-aspect` cascades any behavior change into the scaffolds
  and `skills/_shared`. `arch-upgrade-deps` refreshes Terraform lock
  files and keeps the deployed database's major version.

### Added

- Six lenses: `STO-33`, a data migration wraps its statements in
  `NO FORCE` and `FORCE` in one transaction, and a test over two
  tenants' rows catches a backfill that touched nothing (high);
  `STO-34`, payloads carry ids, and erasure redacts an erased
  subject's audit entries (medium); `CTX-37`, a key's role is the
  lower of its own and its issuer's, checked at every use, and
  removing a membership revokes the member's keys (high); `CTX-38`,
  an agent reaches the operator plane with an operator token of one
  permission, an hour at most, stored as its digest, which
  `admit_operator` admits as the one named exception (high); `DEL-50`,
  the fast rollback (medium); `DEL-51`, a build step holds the push
  credential and nothing that applies, out of `DEL-31` (high). 258
  lenses.
- The changelog guard: `scripts/check_changelog.py`, run by the
  changelog workflow, refuses a pull request that edits
  `CHANGELOG.md` unless it is a release. The changelog lists the
  latest release; every release's notes stay on its GitHub release.
- The pins: every tool version lives in `.github/pins/`, where the
  Makefile and the workflows read it and dependabot updates it.
- CI builds the `arch-check` wheel and runs it and its tests on
  Python 3.11, its floor. `check_skills` requires `allowed-tools` on
  every skill and ops-skill template, and the vocabulary check reads
  the YAML, the JSON, and the docstrings that are published.
- The benchmark's subject reads its own key and runs in an environment
  that never held a judge's key. The workflow redacts every run folder
  before it uploads one, and runs behind the `benchmark` environment.
- The new-issue page links to private vulnerability reporting.

### Fixed

- `arch-check` reads the spellings real code uses: a base imported
  under another name, a stage through `Annotated` or an alias, a
  table renamed by a migration, a quoted identifier, and a client
  built under a top-level `try` or as a default. `CON-06`, `CON-07`,
  `CON-10`, `CON-15`, `STO-18`, `STO-26`, and `OPS-25` read what they
  missed. An unparseable manifest is reported, never read as missing,
  and a local rule reads its own options beside a shipped one.
- The benchmark counts a failed repeat as 0 in the mean, reports the
  spread, and repeats three times by default. The judge prompt fences
  the artifact, a fallback judge is recorded, and `--subject-model`
  pins the subject's model.
- The README's `arch-check` snippet puts the command in a variable, so
  make passes the ref intact.
