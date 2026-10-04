# The ops preamble

Every ops skill that reaches a cloud environment reads this page
before its first step. It says which credentials exist, how a skill
checks the one it holds, what the env file and the provisioner's file
keep, and how a command uses a value from them without showing it.

The rules that stop a secret leaking are stated in the skills
themselves, one line each, because a skill must not need this page to
be safe. What is here is the detail behind them: the profiles, the
commands, the fields, and the way back when a token is gone.

## The environments file

`deployment/cloud/environments.json` names each environment: the
account id, the region, the administrator profile, the Identity Center
profile an operator signs in with, and the three public names. Read it;
never guess a value it holds.

## The profiles

| Profile | Who holds it | What it is for |
|---------|--------------|----------------|
| `acme-<env>-investigate` | every skill that reads an environment | assumes `acme-investigate-<env>`: describes and queries, writes nothing |
| `acme-staging`, `acme-prod` | the person signing in | the Identity Center sign-in the investigate profile chains from (PowerUserAccess, ReadOnlyAccess) |
| `acme-staging-admin`, `acme-prod-admin` | the account's administrator | the bootstrap and the destroy, by hand, once |

A wider credential is not a convenience; it is the boundary gone. A
skill that reads holds the investigate profile and refuses the rest:
the administrator profiles above all, and the sign-in profiles with
them, whose permission sets are wider than the role.

## The check, before any other command

```bash
aws sts get-caller-identity --profile acme-<env>-investigate
```

`Arn` reads
`arn:aws:sts::<account>:assumed-role/acme-investigate-<env>/...`, and
`Account` equals the environment's `account_id` in
`deployment/cloud/environments.json` (read the file; the value is
`.environments.<env>.account_id`). Stop on a mismatch: the right role
in the wrong account is the wrong credential. Every `aws` command a
skill runs carries `--profile acme-<env>-investigate`; `AWS_PROFILE`
is never read as a substitute.

A skill that runs as an account's administrator checks the same two
answers under that profile:

```bash
aws sts get-caller-identity --profile <admin_profile>
```

`Account` is the environment's `account_id`, and `Arn` is an Identity
Center administrator role (`assumed-role/AWSReservedSSO_...`), never
`assumed-role/acme-investigate-*`. When the session has expired, the
person signs in again with `aws sso login --profile <admin_profile>`;
the agent never does it for them.

## The env file

`~/.config/acme/ops/<env>.env` is owner-only and outside the
repository. It holds:

- `ACME_API_URL`, the environment's edge.
- `ACME_OPERATOR_TOKEN`, a `read` operator token.
- `ACME_ERROR_TRACKER_URL` and `ACME_ERROR_TRACKER_TOKEN`, left
  empty by an environment that names no tracker: nothing provisions
  one. A skill that finds them empty reports "not read", never "no
  errors".
- `ACME_ERROR_TRACKER_ORG` and `ACME_ERROR_TRACKER_PROJECT`, which
  name the product's one project and hold the same value in every
  environment: one project takes every environment's errors, and a
  read of it filters on `environment:<env>`. The org is the tracker's
  slug, which need not be the product's name. The create run writes
  them, and the url, from `error_tracker` in
  `deployment/cloud/environments.json`.

`local.env` points at the compose stack and adds the twins,
`ACME_PROMETHEUS_URL` and `ACME_JAEGER_URL`, on the ports `.env`
names. `make seed` writes it, and `local.provisioner.env` beside it,
when each is absent, with the token of the local operator each holds.
A local token that expired is the person's to refresh, like any other:
`uv run acme-ops token --env local --identity operator|provisioner`.

The file holds no password and no TOTP secret: an agent never signs in
with a password. It holds no `write` token either: every skill that
reads sources it.

## The provisioner's file

`~/.config/acme/ops/<env>.provisioner.env`, owner-only, beside the env
file, holds `ACME_PROVISIONER_TOKEN` and nothing else: the provisioner's
`write` token, which creates and removes a traffic run's own tenants.
Only `acme-ops traffic` and `acme-ops stress` read it, from `--env`. No
skill sources it, a skill that drives traffic included, so a session
that reads never holds the write token.

An env file that holds `ACME_PROVISIONER_TOKEN` is refused by every
`acme-ops` command, which prints the line that moves the token into
the provisioner's file without showing it. Stop, and give the
person that line; moving it is theirs, as a token's refresh is.

## Using a value without showing it

The file's values stay out of the conversation, so no skill reads it,
with `Read`, `cat`, or anything else. Shell state does not persist
between calls, so a command that needs a value sources the file and
makes the call in the same command:

```bash
set -a; . ~/.config/acme/ops/<env>.env; set +a
curl -s -H "Authorization: Bearer $ACME_OPERATOR_TOKEN" "$ACME_API_URL/v1/admin/me"
```

`acme-ops` reads the file itself from `--env`, and refuses a file its
group or anyone else can read. No token is printed, by a skill or by
it.

## When a token is refused or expired

A token carries one permission and expires within the hour, and a
person can end it sooner: `uv run acme-ops token --env <env> --list`
and `--revoke <id>`. A revoked token is refused like an expired one. A
skill never lists or revokes a token: ending a credential is the
person's step, as minting one is.

The operator's: stop and ask the person to run `uv run acme-ops token
--env <env> --identity operator` in their own terminal, which signs
them in there and asks there for the TOTP code; never ask for the
sign-in or the code in the conversation.

The provisioner's: stop and ask the person to refresh it, in the cloud
by dispatching `grant-operator.yml` with `mint_token: provisioner`,
then running `uv run acme-ops token --env <env> --identity
provisioner` in their own terminal, which copies the token the grant
job wrote into the provisioner's file under their own sign-in (in
production with `--profile acme-prod-power`), never under an
investigate profile, which reads no secret. Locally, a run without a
token in `local.provisioner.env` is refused before it provisions
anything, saying the environment `holds no provisioner token`, and is
made again with `--orgs 0`.
