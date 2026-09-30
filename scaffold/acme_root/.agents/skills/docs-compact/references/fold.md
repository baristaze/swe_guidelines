# The fold

A fold replaces a role's migration chain with one revision. It holds the
head's schema under the head's revision id, so a database at the head
has nothing to apply, and a reader finds the schema in one file. No
applied file is edited: the chain's files go, and one pair takes their
place. A role whose chain is one revision is folded already.

`<compose>` below is the Makefile's compose command: `docker compose
--env-file .env.example --env-file .env -f
deployment/local/docker-compose.yml`, with `--env-file .env` left out
when the tree has no `.env`. `<day>` is today's date in UTC,
`yyyymmdd`, and `<evidence>` is
`~/Downloads/acme_docs_compact_<yyyy-mm-dd>`.

## The precondition

Every database that exists is at its chain's head. Read each part, and
stop the fold at the first that fails or cannot be read: the chain
stays as it is, and the report says which part.

1. The heads, one line per role:

   ```bash
   uv run --package acme-om python -c "from acme.om.storage.migrate import head; from acme.om.storage.roles import DatabaseRole; [print(r.value, head(r)) for r in DatabaseRole]"
   ```

2. The newest migration's commit: `git log -1 --format=%H origin/<main>
   -- om/migrations/sql om/migrations/versions`.
3. Each environment `deployment/cloud/environments.json` names has
   deployed that commit or a later one. The deploy migrates before it
   rolls out, so its database is then at the head.
   - Staging: `gh run list --workflow deploy-staging.yml --branch <main>
     --status success --limit 1 --json headSha --jq '.[0].headSha'`.
   - Production: the tip of `origin/release`, when `gh api
     repos/{owner}/{repo}/commits/<tip>/statuses --jq '[.[] |
     select(.context == "released/production")] | first | .state'`
     prints `success`.
   - For each: `git merge-base --is-ancestor <newest migration's commit>
     <deployed commit>` exits 0.
   - An environment with no successful deploy and no `release` branch
     has no database, and passes. A `gh` call that fails does not.
4. The local database: for every role, `make migrate` prints
   `<role>: upgraded to <head>`.
5. No expand and contract is half shipped: a column, a default, or a
   trigger the chain added for the release before is in every
   environment of part 3. The fold keeps such a piece as the head has
   it; its contract step comes later, as a revision on the fold.

## The steps

1. **Dump the chain.** Before any file changes, make a database from
   the chain and dump each role that has more than one revision:

   ```bash
   uv run python ops/audit/auditdb.py create audit_fold_chain_<day>
   <compose> exec -T postgres pg_dump -U postgres -d audit_fold_chain_<day> --schema-only --schema=<role> --restrict-key=fold > <evidence>/chain.<role>.sql
   ```

   When `auditdb.py list` shows the name taken, another run holds it:
   add a suffix (`_2`), and never drop a database this run did not make.
2. **Write the fold**, for each such role. The revision id is the head's
   (part 1), whole, a suffix included.
   - `om/migrations/sql/<role>/<head id>_the_<role>_role.up.sql`: the
     schema as `chain.<role>.sql` has it, written as the chain's own
     files are: a comment that says what the role holds, every name
     schema-qualified, each table with its columns in the dump's order
     (a column a later step added sits where the dump has it), then its
     constraints, indexes, policies, and grants, and each function or
     trigger the head still has. No `SET`, no `OWNER TO`, and no
     `alembic_version`: the runner makes that table.
   - No data statement: a backfill leaves with the step it belonged to,
     since a database at the head has run it and a new one has no row to
     fill. A row every new database must hold, which the code reads and
     never writes, stays, and the report lists it.
   - `<head id>_the_<role>_role.down.sql`: drops what the up file
     makes, grants first.
   - `om/migrations/versions/<role>/<head id>_the_<role>_role.py`: the
     wrapper, as the chain's are, with `revision = "<head id>"` and
     `down_revision = None`.
   - Every other file of the role's chain goes: `git rm`.
3. **Move what named a step.** `git grep -n` each removed stamp and each
   removed file name.
   - A test that pins a removed revision id, or tests a backfill that
     left, goes with its step. The round trip of the head and the
     ORM-against-schema check stay.
   - An ADR, a comment, or a document that cites a removed revision or
     file names the fold's file, or drops the citation when it named a
     step and not the schema.
   - A checker exception whose `path` is a removed migration file
     (`[[tool.arch-check.exception]]` in `pyproject.toml`) names the
     fold's file when the fold still holds what it allows, and goes when
     it does not.
   - An entry of `DROPPED_TABLE_ROLES` in
     `om/src/acme/om/storage/roles.py` names a table only a removed
     step named. It is code: leave it, and list it in the report.
   - `deployment/migration-inputs.json` covers the fold's files by
     pattern, so it does not change. The fingerprint does: the next
     deploy of each environment runs the migrate task once, which finds
     its database at the head and applies nothing.
4. **Prove it.** Make a database from the fold and dump it the same way:

   ```bash
   uv run python ops/audit/auditdb.py create audit_fold_folded_<day>
   <compose> exec -T postgres pg_dump -U postgres -d audit_fold_folded_<day> --schema-only --schema=<role> --restrict-key=fold > <evidence>/fold.<role>.sql
   diff <evidence>/chain.<role>.sql <evidence>/fold.<role>.sql
   ```

   `diff` prints nothing, for every role. When it prints a difference,
   correct the fold's SQL, drop `audit_fold_folded_<day>`, and prove it
   anew: the first run plus at most 3 reruns. Then stop: `git restore
   --staged --worktree om/migrations`, and report the difference.
5. **A database at the old head has nothing to apply.** The chain's
   database is one. Run the fold's migrate and its check on it:

   ```bash
   uv run python -c "import os, sys; sys.path.insert(0, 'ops/audit'); import auditdb; from acme.om.storage import migrate; os.environ.update(auditdb.urls('audit_fold_chain_<day>')); sys.exit(migrate.main(['upgrade', '--all', '--local']) or migrate.main(['check', '--all', '--local']))"
   ```

   It prints `<role>: upgraded to <head id>` and then `<role>: in sync`
   for every role, and exits 0. Dump each folded role once more, into
   `<evidence>/after.<role>.sql`: `diff` against `chain.<role>.sql`
   prints nothing.
6. **Drop both databases**, whatever happened before:

   ```bash
   uv run python ops/audit/auditdb.py drop audit_fold_chain_<day>
   uv run python ops/audit/auditdb.py drop audit_fold_folded_<day>
   ```

7. **Commit the fold alone**: the subject `Each migration chain is one
   revision at its head`, and in the body, per role, the head id, the
   count of revisions folded, the backfills left out, the tests that
   went, and the citations that moved. Note the commit before it: the
   report names it as the last one whose chain reaches the head from an
   older revision.

## What the report says of it

- Per role: the head id, the revisions folded, the backfills left out,
  and that the three dumps are equal.
- The last commit before the fold. A snapshot taken at an older
  revision is restored at that commit's release, which migrates it to
  the head, and then moves to the current release.
- A developer's local database behind the head is made anew (`make
  reset`); one at the head needs nothing.
