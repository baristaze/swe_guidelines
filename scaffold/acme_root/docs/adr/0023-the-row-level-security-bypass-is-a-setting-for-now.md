# ADR 0023: The row-level security bypass is a setting, for now

**Status**: closed (2026-09-22). The deviation is over.
[ACME-54](https://tracker.example/issue/ACME-54) and
[ACME-56](https://tracker.example/issue/ACME-56) split the logins
(release A, below); release B took the master out of the clause (Closed
below). The record stays for the interval it covers.

## Context

Row-level security is the second tenant fence
([ADR 0016](0016-row-level-security-is-the-second-fence.md)). Every
`tenant_fence` policy admits a row whose `org_id` equals
`current_setting('app.org_id')`. It also admits every row when that
setting is the system scope, the all-zero UUID, which is how the sweep
and the relay read across tenants.

This bypass is a setting. The runtime login can write it in any
session with
`SET app.org_id = '00000000-0000-0000-0000-000000000000'`. The first
fence is the code, where every storage method takes the tenant and the
funnel sets the scope per transaction. So the second fence holds
against a mistake in the code, but not against SQL injected past it:
an injected `SET` lifts the second fence as well.

## Decision

The bypass stays a setting until one login is split into several.
There are three logins to split out: a migration login that owns the
schema (ACME-54), a sweeper login for the cross-tenant paths (ACME-56),
and the runtime login. Once the sweeper login exists, the bypass clause
moves into policies declared `TO` it, and the runtime login's policies
carry no bypass.

It is not done here because it changes every policy migration, the
database URL of each role, the migration task's secret, and the
integration engines, in one change. It is also one design with ACME-54's
owner and DML split.

## Consequences

- Tenant isolation against injection rests on the first fence alone:
  every query is parameterised through SQLAlchemy, and no statement is
  built from a string.
- The negative-control test and the policy check test stay as they are.
  They prove the fence against a missing predicate, which is the case
  it covers today.

## Amended: release A

The logins are split. Three logins reach the database, and none is a
superuser or carries BYPASSRLS:

- `acme_migration` owns every role schema, table, sequence, and
  version table, and runs the migrations.
- `acme_runtime` is every request's connection. It owns nothing and
  holds DML only.
- `acme_system` is its twin for the system scope, on a pool of its
  own. The funnel opens the system scope on it and every other scope
  on the runtime login.

`acme-api migrate ensure-logins` makes them, run as the master by the
migrate task before `migrate --all`.

The bypass is no longer a setting alone. The system-scope clause of
every `tenant_fence` policy is now:

```sql
current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
AND current_user IN ('acme_system', 'acme')
```

The runtime login naming the system scope reads nothing, so an injected
`SET` on a request's connection no longer lifts the second fence.

`acme`, the master, stays in the clause for release A only. It is the
login every task of the release before connects as, and those tasks keep
serving while release A rolls out beside them. Until release B, the
master's password can still read across tenants through the setting.
That login is held by the migrate task alone once release A is live.

Release B drops `'acme'` from the clause, in a migration of its own,
after every task of the release before has stopped. This record closes
with it. [ADR 0026](0026-what-0-31-0-leaves-as-a-choice.md) lists what
else release B removes.

## Closed: release B

The system-scope clause of every `tenant_fence` policy is now:

```sql
current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
AND current_user = 'acme_system'
```

One migration per chain (`202609240000_system_login_alone`) alters every
policy to it, and the down puts the master back beside the system login.
The bypass is no longer a setting any login can write: it is the system
login, on a pool of its own, naming the system scope. The runtime login
naming it reads nothing, and neither does the migration login serving a
request, because no request reaches it.

The master, `acme`, is what the migrate task connects as for
`ensure-logins` and nothing else. It owns no policy clause now, so its
password no longer reads across tenants.

The fence test asserts each policy names `acme_system` and no login
beside it.
