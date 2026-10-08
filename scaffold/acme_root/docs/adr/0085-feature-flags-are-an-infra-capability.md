# ADR 0085: Feature flags are an infra capability, with the provider chosen at boot

**Status**: accepted (2026-10-08)

## Context

A release toggle turns unfinished work off in production, and a kill
switch turns a failing feature off without a deploy. Both decide at
runtime, per org and sometimes per person, whether a path runs. A
vendor's flag client called straight from a manager puts that vendor's
code at the heart of the system, where every other capability is an
interface with swappable impls.

What a tenant may do, or what a plan allows, is another thing: a
modelled entity with a manager and storage, never a flag.

## Decision

- **Flags are a capability like the cache and the secrets.**
  `FlagsInterface` is reached through `InfraInterface.get_flags()`, and a
  manager takes it in its constructor. One call,
  `evaluate(org_id, user_id=None)`, answers a `FlagSet`: every declared
  flag's value for that audience. A server check reads one value; a
  client's snapshot reads the flags marked for clients, through
  `GET /v1/flags`, with an `ETag` and `304`.
- **Flags are declared in code.** `Flag` is a `StrEnum`, and each flag
  has a default and a mark that says whether a client may read it. A
  flag the provider does not know, or a provider that fails, reads its
  default, and `evaluate` never raises.
- **One precedence in every impl.** A rule on the user wins over a rule
  on the org, that over the provider's default for the flag, and that
  over the code's. The user is the identity inside one org, one per
  membership: `TenantContext.user_id`.
- **The deployed impl evaluates in the process through OpenFeature.**
  The vendor's provider is chosen at boot from settings, inside
  `acme.infra.flags`, and nothing else imports OpenFeature or a
  vendor's SDK. The targeting key is the org, so a percentage rollout
  is sticky per org; `org_id` and `user_id` are attributes, so a rule
  targets either. The scaffold names LaunchDarkly, whose Developer plan
  costs nothing, with its SDK key injected at start as the identity
  provider's key is.
- **The backend is a setting.** `ACME_FLAGS_BACKEND` is `memory` (a
  rules file, local and tests only, refused in a deployed environment),
  `launchdarkly` (refused without its key, in every environment), or
  `none`, where every flag reads its default and the start line says
  so. A deployed environment starts on `none` and moves to
  `launchdarkly` once its key is in the secret store.
- **The flag provider is chosen apart from the identity provider.** No
  flag impl imports the identity integration or tenancy. The identity
  provider's own flag service is not an impl:
  - it targets its own organizations and users, so the impl would need
    tenancy's mapping from an org to its organization at the provider;
  - an org that signs in otherwise would have no flags;
  - it cannot target a person inside an org;
  - it costs a call per evaluation.
- **The first flag is `media-uploads`.** It defaults on, clients may
  read it, and `create_file` refuses a new upload with `feature_off`
  (403) where it is off for the caller's audience.

## Consequences

- A manager knows `FlagsInterface` alone, and arch-check's DEL-22
  decides an import of OpenFeature or a flag vendor's SDK anywhere else.
- Another vendor is another OpenFeature provider, chosen in
  `acme.infra.flags`; no manager changes.
- An evaluation is in memory: the vendor's SDK streams its rules into
  the process, and a request pays no call. A process that cannot reach
  the vendor at boot waits at most `ACME_FLAGS_TIMEOUT_SECONDS`, runs on
  the code's defaults, and says so.
- A client hides what a flag turns off, and the server refuses it
  whatever the client shows.
- In LaunchDarkly, a rule on `user_id` sits above a rule on `org_id`,
  since rules match in order, and an individual target on an org's key
  matches before both; the runbook says so.
