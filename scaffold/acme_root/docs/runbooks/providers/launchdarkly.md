# LaunchDarkly: the flag provider

A feature flag is declared in code, in `Flag`
([`flags/__init__.py`](../../../infra/src/acme/infra/flags/__init__.py)),
with its default and whether a client may read it. In a deployed
environment its rules come from LaunchDarkly, evaluated in the process
through OpenFeature
([ADR 0085](../../adr/0085-feature-flags-are-an-infra-capability.md)).
This page says what is set by hand once, how a rule reaches an org or a
person, and what to do when it breaks.

## The rule

**A rule targets the org, then the person.** Each evaluation sends one
context of kind `org`, keyed by the org's id, with the attributes
`org_id` and `user_id`. A percentage rollout buckets by that key, so the
people of one org see one value. A person is targeted by a rule on
`user_id`, and an org by a rule on `org_id`.

**A person's rule sits above its org's.** Rules match in order, so the
code's order holds only when the rules on `user_id` come first. An
individual target on an org's key matches before every rule: use it for
an org whose people never differ.

**A flag is a release toggle or a kill switch.** What a tenant may do,
what a plan allows, is a modelled entity, never a flag.

## Environments

| Acme environment | LaunchDarkly environment | `ACME_FLAGS_BACKEND` |
|------------------|--------------------------|----------------------|
| `local` | none: `.local/flags.json` | `memory` |
| `staging` | Test | `none`, then `launchdarkly` once its key is set |
| `production` | Production | `none`, then `launchdarkly` once its key is set |

`flags_backend` in each Terraform root sets the backend. With `none`,
every flag reads the default its declaration gives, and the start line
says so.

## First-time setup, once per LaunchDarkly project

1. Make a project `acme` on the Developer plan. It comes with a Test and
   a Production environment.
2. For each member of `Flag`, make a boolean flag with the same key,
   such as `media-uploads`. In each environment, set its default rule
   to serve the code's default and its off variation to `false`, then
   turn targeting on. LaunchDarkly makes a flag with targeting off, and
   targeting off serves the off variation: a flag left so reads `false`
   for every org, whatever the code's default.
3. Copy each environment's server-side SDK key, from the environment's
   settings: never its client-side id or its mobile key.

All three come before an environment's backend becomes `launchdarkly`.
From then on, turning a flag's targeting off is its kill switch: it
reads `false` everywhere.

A flag LaunchDarkly does not know reads the code's default, so a new
flag ships before its LaunchDarkly flag exists. Its flag is then made as
step 2 says: from its making until its targeting is on, an environment
on `launchdarkly` reads it `false`.

## The secret of a deployed environment

The first deploy makes the secret in the environment's account, holding
`off`:

| Secret | Setting | Read by |
|--------|---------|---------|
| `acme/<env>/launchdarkly_sdk_key` | `ACME_LAUNCHDARKLY_SDK_KEY` | the API, the worker, and the one-off tasks |

Write the key under your own sign-in, then switch the backend:

```bash
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
aws sso login --profile acme-staging
read -rs VALUE
aws secretsmanager put-secret-value --profile acme-staging --region us-west-2 \
  --secret-id acme/staging/launchdarkly_sdk_key --secret-string "$VALUE"
unset VALUE
```

Then set `flags_backend = "launchdarkly"` in the environment's root, in
a pull request, and deploy. The key goes first: a process told
`launchdarkly` without it refuses to start. Production's everyday
profile only reads; writing there takes `acme-prod-power`, and only for a
change a person authorized.

**Check.** The start line in `/acme/<env>/api` names
`flags=launchdarkly(openfeature)`. Then `GET /v1/flags` as a person of a
targeted org answers that org's value.

## Local development

The local stack reads the rules file on every evaluation, so an edit
applies on the next request:

```json
{"media-uploads": {"default": true, "orgs": {"<org id>": false}, "users": {"<user id>": true}}}
```

A missing file holds no rules. To try LaunchDarkly locally, export the
Test environment's key and `ACME_FLAGS_BACKEND=launchdarkly`. A key is
never committed.

## Rotation

1. Reset the environment's SDK key in LaunchDarkly, with the old one
   expiring later.
2. Write the new key, and roll the API and the worker.
3. Check the start lines before the old key expires.

## When it breaks

| What you see | Why | Where to look |
|--------------|-----|---------------|
| A process stops at start: `ACME_FLAGS_BACKEND=launchdarkly is refused without ACME_LAUNCHDARKLY_SDK_KEY` | The secret is `off` | Write it, and roll |
| `flags provider launchdarkly not ready`, and every flag reads its default | The key is another environment's or revoked, or LaunchDarkly is unreachable | The key; the SDK keeps trying, and its rules take over once it connects |
| A flag reads its default everywhere | Its key differs from the code's | The flag's key in LaunchDarkly |
| A flag reads `false` everywhere | Its targeting is off, which serves the off variation, `false`: the kill switch, not the default | The flag's targeting in that environment |
| A person's rule does not apply | An individual target on the org, or a rule on `org_id` above it, matches first | The flag's rule order |
| A process stops at start: `ACME_FLAGS_BACKEND=memory is refused` | The memory backend is local only | `flags_backend` in the Terraform root |

## When an environment is torn down

`scripts/cloud_nuke.sh` does not touch LaunchDarkly. The project, its
flags, and their rules stay for the next environment. Reset the key if
the environment is not coming back.
