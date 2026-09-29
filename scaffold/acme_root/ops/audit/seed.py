"""A heavy, synthetic seed for an audit database, at a stated scale.

    uv run python ops/audit/seed.py audit_<run> --scale 1

Scale 1 is the shape the query audit measures against: 5,000 people, each
with a personal org, and one heavy team org of 200 members holding 100,000
tasks (40,000 open, 15,000 done, 40,000 archived, 5,000 deleted) and a
million events; beside them work items, outbox rows, sessions, idempotency
records, files, invitations, API keys, Slack installations, and cleanup
records, each in the proportions a busy platform has. Every count scales
linearly with `--scale`, with a floor of one, so `--scale 0.01` is a quick
run with the same shape. The heavy org's id is fixed, so a statement file
can name it (see `FIXED`). Its last member (`n` = the member count) is
assigned no task and created none, so an audit always has an idle member.

It runs as the local superuser on a database `auditdb.py` made, since no
login the application holds walks past the row-level security policies,
and it refuses any other database. Then it runs `ANALYZE`, so the planner
reads the seeded tables as they are.
"""

import argparse
import asyncio
import math
import sys
import time
from dataclasses import dataclass

from auditdb import check_name, superuser_on
from sqlalchemy.ext.asyncio import create_async_engine

# Ids a statement file can name: the heavy org and the system scope.
FIXED = {
    "heavy_org": "01900000-0000-7000-8000-00000000b16b",
    "system": "00000000-0000-0000-0000-000000000000",
}
EMPTY = FIXED["system"]


@dataclass(frozen=True)
class Counts:
    """How many of each thing a scale seeds. Scale 1 is the audit's heavy seed."""

    people: int
    members: int
    left: int
    open_tasks: int
    done_tasks: int
    archived_tasks: int
    deleted_tasks: int
    events: int
    work_items: int
    outbox_rows: int
    idempotency: int
    files: int

    @classmethod
    def at(cls, scale: float) -> Counts:
        if not 0 < scale <= 10:
            raise SystemExit("--scale is above 0 and at most 10")

        def n(full: int) -> int:
            return max(1, math.ceil(full * scale))

        people = max(3, n(5000))
        members = max(2, min(200, people - 1))
        return cls(
            people=people,
            members=members,
            left=min(n(150), people - members),
            open_tasks=n(40000),
            done_tasks=n(15000),
            archived_tasks=n(40000),
            deleted_tasks=n(5000),
            events=n(1000000),
            work_items=n(50000),
            outbox_rows=n(100000),
            idempotency=n(50000),
            files=n(20000),
        )


def statements(c: Counts) -> list[tuple[str, str]]:
    """The seed, one named statement at a time, in the order they must run."""
    big = f"'{FIXED['heavy_org']}'::uuid"
    work_done = c.work_items * 30 // 50
    work_failed = work_done + c.work_items * 2 // 50
    work_future = work_failed + c.work_items * 12 // 50
    work_ready = work_future + c.work_items * 5 // 50
    outbox_done = c.outbox_rows * 95 // 100
    outbox_pending = outbox_done + c.outbox_rows * 3 // 100
    deleted_young = c.deleted_tasks * 9 // 10
    return [
        ("settings", "SET synchronous_commit = off"),
        ("random seed", "SELECT setseed(0.42)"),
        (
            "identities",
            f"""INSERT INTO core.identities (id, created_at, updated_at, created_by, email,
                updated_by, time_zone)
            SELECT uuidv7(-(interval '400 days') + g * interval '1 minute'),
                now() - interval '400 days', now(),
                   '{EMPTY}', 'person' || g || '@example.com', '{EMPTY}', 'UTC'
            FROM generate_series(1, {c.people}) g""",
        ),
        (
            "people",
            """CREATE TEMP TABLE people AS
            SELECT row_number() OVER (ORDER BY id) AS n, id AS identity_id,
                   uuidv7(-(interval '399 days') + row_number() OVER (ORDER BY id) * interval '1
                       minute') AS org_id,
                   gen_random_uuid() AS user_id
            FROM core.identities""",
        ),
        (
            "personal orgs",
            """INSERT INTO core.orgs (id, org_id, name, created_at, updated_at, created_by,
                slug, updated_by, kind, personal_identity_id)
            SELECT org_id, org_id, 'Personal ' || n, now() - interval '399 days', now(),
                identity_id, 'p-' || n,
                   identity_id, 'personal', identity_id
            FROM people""",
        ),
        (
            "personal users",
            """INSERT INTO core.users (id, org_id, created_at, updated_at, created_by,
                identity_id, email, display_name, updated_by)
            SELECT user_id, org_id, now() - interval '399 days', now(), identity_id, identity_id,
                   'person' || n || '@example.com', 'Person ' || n, identity_id
            FROM people""",
        ),
        (
            "personal memberships",
            """INSERT INTO core.memberships (id, org_id, created_at, updated_at, created_by,
                user_id, role, teams, updated_by)
            SELECT gen_random_uuid(), org_id, now() - interval '399 days', now(), identity_id,
                user_id, 'owner', '[]', identity_id
            FROM people""",
        ),
        (
            "heavy org",
            f"""INSERT INTO core.orgs (id, org_id, name, created_at, updated_at, created_by,
                slug, updated_by, kind)
            SELECT {big}, {big}, 'Heavy Co', now() - interval '2 years', now(), identity_id,
                'heavy', identity_id, 'team'
            FROM people WHERE n = 1""",
        ),
        (
            "heavy members",
            f"""CREATE TEMP TABLE members AS
            SELECT n, identity_id,
                uuidv7(-(interval '700 days') + n * interval '1 minute') AS user_id
            FROM people WHERE n <= {c.members}""",
        ),
        (
            "heavy users",
            f"""INSERT INTO core.users (id, org_id, created_at, updated_at, created_by,
                identity_id, email, display_name, updated_by)
            SELECT user_id, {big}, now() - interval '700 days', now(), identity_id, identity_id,
                   'person' || n || '@example.com', 'Member ' || n, identity_id
            FROM members""",
        ),
        (
            "heavy memberships",
            f"""INSERT INTO core.memberships (id, org_id, created_at, updated_at, created_by,
                user_id, role, teams, updated_by)
            SELECT gen_random_uuid(), {big}, now() - interval '700 days', now(), identity_id,
                user_id,
                   CASE WHEN n = 1 THEN 'owner' ELSE 'member' END, '[]', identity_id
            FROM members""",
        ),
        (
            "departed members",
            f"""INSERT INTO core.users (id, org_id, created_at, updated_at, created_by,
                identity_id, email, display_name,
                                    updated_by, deleted_at, deleted_by)
            SELECT gen_random_uuid(), {big}, now() - interval '500 days', now(), identity_id,
                identity_id,
                   'left' || n || '@example.com', 'Left ' || n, identity_id,
                       now() - (n % 60) * interval '1 day', identity_id
            FROM people WHERE n > {c.members} AND n <= {c.members + c.left}""",
        ),
        (
            "billing accounts",
            """INSERT INTO core.billing_accounts (id, org_id, created_at, updated_at,
                created_by, updated_by,
                                              cancel_at_period_end, quantity, comped_plan)
            SELECT gen_random_uuid(), id, now(), now(), created_by, created_by, false, 1,
                   CASE WHEN slug = 'heavy' THEN 'max' END
            FROM core.orgs""",
        ),
        (
            "member array",
            """CREATE TEMP TABLE bu AS
            SELECT array_agg(user_id ORDER BY n) AS u, count(*)::int AS k FROM members""",
        ),
        (
            "open tasks",
            f"""INSERT INTO core.tasks (id, org_id, created_at, updated_at, created_by, title,
                notes, status, assignee_id,
                                    rank, updated_by, version, due_on)
            SELECT uuidv7(-(interval '700 days') + g * interval '10 minutes'), {big},
                   now() - interval '700 days' + g * interval '10 minutes',
                       now() - (random() * 300) * interval '1 day',
                   bu.u[1 + (g * 7) % greatest(bu.k - 1, 1)], 'Open task ' || g, '', 'open',
                   CASE WHEN random() < 0.7 THEN bu.u[1 + (g * 13) % greatest(bu.k - 1, 1)] END,
                   g::numeric, bu.u[1], 1,
                   CASE WHEN random() < 0.3 THEN current_date + (random() * 60)::int END
            FROM generate_series(1, {c.open_tasks}) g, bu""",
        ),
        (
            "done tasks",
            f"""INSERT INTO core.tasks (id, org_id, created_at, updated_at, created_by, title,
                notes, status, assignee_id,
                                    rank, updated_by, version)
            SELECT uuidv7(-(interval '700 days') + g * interval '9 minutes'), {big},
                   now() - interval '700 days' + g * interval '9 minutes',
                       now() - (random() * 89) * interval '1 day',
                   bu.u[1 + (g * 7) % greatest(bu.k - 1, 1)], 'Done task ' || g, '', 'done',
                   CASE WHEN random() < 0.7 THEN bu.u[1 + (g * 13) % greatest(bu.k - 1, 1)] END,
                   g::numeric, bu.u[1], 2
            FROM generate_series(1, {c.done_tasks}) g, bu""",
        ),
        (
            "archived tasks",
            f"""INSERT INTO core.tasks (id, org_id, created_at, updated_at, created_by, title,
                notes, status, assignee_id,
                                    rank, updated_by, version, archived_at)
            SELECT uuidv7(-(interval '720 days') + g * interval '15 minutes'), {big},
                   now() - interval '720 days' + g * interval '15 minutes', ts,
                       bu.u[1 + (g * 7) % greatest(bu.k - 1, 1)],
                   'Archived task ' || g, '', 'done',
                   CASE WHEN random() < 0.7 THEN bu.u[1 + (g * 13) % greatest(bu.k - 1, 1)] END,
                   g::numeric, bu.u[1], 3, ts
            FROM (SELECT g, now() - (random() * 700) * interval '1 day' AS ts
                  FROM generate_series(1, {c.archived_tasks}) g) s, bu""",
        ),
        (
            "deleted tasks",
            f"""INSERT INTO core.tasks (id, org_id, created_at, updated_at, created_by, title,
                notes, status, assignee_id,
                                    rank, updated_by, version, deleted_at, deleted_by)
            SELECT uuidv7(-(interval '400 days') + g * interval '1 hour'), {big},
                now() - interval '400 days', dt,
                   bu.u[1], 'Deleted task ' || g, '',
                       CASE WHEN g % 2 = 0 THEN 'open' ELSE 'done' END,
                   NULL, g::numeric, bu.u[1], 2, dt, bu.u[1]
            FROM (SELECT g,
                CASE WHEN g <= {deleted_young} THEN now() - (random() * 29) * interval '1 day'
                                 ELSE now() - (31 + random() * 5) * interval '1 day' END AS dt
                  FROM generate_series(1, {c.deleted_tasks}) g) s, bu""",
        ),
        (
            "personal tasks",
            """INSERT INTO core.tasks (id, org_id, created_at, updated_at, created_by, title,
                notes, status,
                                   rank, updated_by, version)
            SELECT uuidv7(-(interval '100 days') + (p.n * 5 + k) * interval '1 second'), p.org_id,
                   now() - interval '100 days', now() - (k * interval '3 days'), p.user_id,
                       'Task', '',
                   CASE WHEN k <= 3 THEN 'open' ELSE 'done' END, k::numeric, p.user_id, 1
            FROM people p CROSS JOIN generate_series(1, 5) k""",
        ),
        (
            "tenant sessions",
            f"""INSERT INTO core.sessions (id, org_id, created_at, updated_at, created_by,
                identity_id, user_id, token_hash,
                                       credential_kind, expires_at, updated_by, last_seen_at)
            SELECT uuidv7(-(interval '20 days') + (p.n * 2 + k) * interval '1 second'),
                p.org_id, now() - interval '20 days',
                   now(), p.identity_id, p.identity_id, p.user_id,
                   encode(sha256(convert_to('s' || p.n || '-' || k, 'UTF8')), 'hex'),
                       'session_token',
                   now() + interval '10 days', p.identity_id, now() - interval '5 minutes'
            FROM people p CROSS JOIN generate_series(1, 2) k
            UNION ALL
            SELECT uuidv7(-(interval '20 days') + (m.n * 2 + k) * interval '1 second'), {big},
                now() - interval '20 days',
                   now(), m.identity_id, m.identity_id, m.user_id,
                   encode(sha256(convert_to('b' || m.n || '-' || k, 'UTF8')), 'hex'),
                       'session_token',
                   now() + interval '10 days', m.identity_id, now() - interval '5 minutes'
            FROM members m CROSS JOIN generate_series(1, 2) k""",
        ),
        (
            "sign-in sessions",
            f"""INSERT INTO core.sessions (id, org_id, created_at, updated_at, created_by,
                identity_id, user_id, token_hash,
                                       credential_kind, expires_at, updated_by)
            SELECT uuidv7(-(interval '20 days') + (p.n * 2 + k) * interval '1 second'), '{EMPTY}',
                   now() - interval '20 days', now(), p.identity_id, p.identity_id, '{EMPTY}',
                   encode(sha256(convert_to('l' || p.n || '-' || k, 'UTF8')), 'hex'), 'login',
                   now() - interval '5 days' + k * interval '10 days', p.identity_id
            FROM people p CROSS JOIN generate_series(1, 2) k""",
        ),
        (
            "api keys",
            f"""INSERT INTO core.api_keys (id, org_id, name, created_at, updated_at, created_by,
                user_id, key_hash, role,
                                       expires_at, updated_by)
            SELECT gen_random_uuid(), {big}, 'key', now(), now(), m.identity_id, m.user_id,
                   encode(sha256(convert_to('k' || m.n || '-' || k, 'UTF8')), 'hex'), 'member',
                   now() + interval '300 days', m.identity_id
            FROM members m CROSS JOIN generate_series(1, 2) k
            UNION ALL
            SELECT gen_random_uuid(), p.org_id, 'key', now(), now(), p.identity_id, p.user_id,
                   encode(sha256(convert_to('pk' || p.n, 'UTF8')), 'hex'), 'member',
                   now() + interval '300 days', p.identity_id
            FROM people p""",
        ),
        (
            "invitations",
            """INSERT INTO core.invitations (id, org_id, created_at, updated_at, created_by,
                updated_by, email, role,
                                         provider_invitation_id, state, expires_at)
            SELECT gen_random_uuid(), p.org_id, now() - interval '10 days',
                now() - interval '5 days', p.identity_id,
                   p.identity_id, 'invitee' || p.n || '-' || k || '@example.com', 'member',
                       'inv_' || p.n || '_' || k,
                   CASE WHEN k = 1 THEN 'pending' ELSE 'accepted' END, now() + interval '5 days'
            FROM people p CROSS JOIN generate_series(1, 4) k""",
        ),
        (
            "idempotency records",
            f"""INSERT INTO core.idempotency_records (id, org_id, user_id, key, request_digest,
                status, body, created_at,
                                                  target_id, attempt_id)
            SELECT uuidv7(-(interval '24 hours') + g * interval '1.7 seconds'), {big},
                bu.u[1 + g % bu.k], 'key-' || g,
                   'digest', 201, '{{}}',
                       now() - interval '24 hours' + g * (interval '86000 seconds' /
                           {c.idempotency}),
                   gen_random_uuid(), NULL::uuid
            FROM generate_series(1, {c.idempotency}) g, bu
            UNION ALL
            SELECT gen_random_uuid(), p.org_id, p.user_id, 'k-' || k, 'd', 201, '{{}}',
                now() - interval '20 hours',
                   gen_random_uuid(), NULL
            FROM people p CROSS JOIN generate_series(1, 20) k""",
        ),
        (
            "files",
            f"""INSERT INTO core.files (id, org_id, name, created_at, updated_at, created_by,
                updated_by, key, extension,
                                    content_type, size_bytes, purpose, subject_id, status,
                                        deleted_at)
            SELECT uuidv7(-(interval '300 days') + g * interval '20 minutes'), {big}, 'f' || g,
                   now() - interval '300 days', now(), bu.u[1], bu.u[1], 'k' || g, 'pdf',
                       'application/pdf', 100000,
                   'task_attachment', t.id,
                   CASE WHEN g % 50 = 0 THEN 'pending' ELSE 'stored' END,
                   CASE WHEN g % 40 = 0 THEN now() - interval '10 days' END
            FROM generate_series(1, {c.files}) g, bu,
                 LATERAL (SELECT id FROM core.tasks WHERE org_id = {big} AND status = 'open'
                          ORDER BY id OFFSET (g % least(5000, {c.open_tasks})) LIMIT 1) t
            UNION ALL
            SELECT gen_random_uuid(), p.org_id, 'f', now() - interval '30 days', now(),
                p.user_id, p.user_id, 'k', 'pdf',
                   'application/pdf', 1000, 'task_attachment', gen_random_uuid(), 'stored', NULL
            FROM people p CROSS JOIN generate_series(1, 10) k""",
        ),
        (
            "slack installations",
            """INSERT INTO core.slack_installations (id, org_id, created_at, updated_at,
                created_by, updated_by, team_id,
                                                 team_name, app_id, bot_user_id, scopes,
                                                     installed_by_slack_user,
                                                 credential_ref, status)
            SELECT gen_random_uuid(), org_id, now(), now(), identity_id, identity_id,
                'T' || md5(org_id::text), 'team',
                   'A1', 'U1', 'chat:write', 'U2', 'ref', 'ok'
            FROM people""",
        ),
        (
            "events",
            f"""INSERT INTO activity.events (id, org_id, seq, kind, target_id, produced_at,
                actor_id, request_id, app, payload)
            SELECT uuidv7(-(interval '100 days') + g * (interval '100 days' / {c.events})),
                {big}, g, 'tasks.task.updated',
                   gen_random_uuid(),
                       now() - interval '100 days' + g * (interval '100 days' / {c.events}),
                   gen_random_uuid(), gen_random_uuid(), 'portal', '{{}}'::jsonb
            FROM generate_series(1, {c.events}) g
            UNION ALL
            SELECT uuidv7(-(interval '10 days') + (p.n * 10 + k) * interval '1 second'),
                p.org_id, k, 'tasks.task.created',
                   gen_random_uuid(), now() - interval '10 days', p.identity_id,
                       gen_random_uuid(), 'portal', '{{}}'
            FROM people p CROSS JOIN generate_series(1, 10) k""",
        ),
        (
            "event cursors",
            f"""INSERT INTO activity.event_cursors (org_id, head, floor)
            SELECT {big}, {c.events}, 0 UNION ALL SELECT org_id, 10, 0 FROM people""",
        ),
        (
            "work items",
            f"""INSERT INTO queue.work_items (id, org_id, created_at, updated_at, created_by,
                kind, target_id,
                                          idempotency_key, payload, lane, status, available_at,
                                              attempts, max_attempts,
                                          updated_by, request_id, claimed_by, lease_expires_at,
                                              claim_token)
            SELECT uuidv7(-(interval '40 days') + g * interval '1 minute'),
                   CASE WHEN g % 3 = 0 THEN {big} ELSE p.org_id END,
                   now() - interval '40 days', upd, '{EMPTY}', kind, gen_random_uuid(),
                       gen_random_uuid(), '{{}}',
                   'default', status, avail, att, 5, '{EMPTY}', gen_random_uuid(),
                   CASE WHEN status = 'claimed' THEN 'worker-1' END,
                   CASE WHEN status = 'claimed' THEN CASE WHEN g % 5 = 0 THEN now() - interval
                       '5 minutes'
                                                          ELSE now() + interval '1 minute' END END,
                   CASE WHEN status = 'claimed' THEN gen_random_uuid() END
            FROM (
              SELECT g,
                     CASE WHEN g <= {work_done} THEN 'done' WHEN g <= {work_failed} THEN 'failed'
                          WHEN g <= {work_ready} THEN 'queued' ELSE 'claimed' END AS status,
                     CASE WHEN g > {work_failed} AND g <= {work_future} THEN 'TASK_REMINDER'
                          WHEN g % 4 = 0 THEN 'SLACK_POST' ELSE 'NOOP' END AS kind,
                     CASE WHEN g > {work_failed} AND g <= {work_future} THEN now() + (random() *
                         60) * interval '1 day'
                          ELSE now() - (random() * 60) * interval '1 minute' END AS avail,
                     CASE WHEN g <= {work_failed} THEN now() - (random() * 40) * interval '1 day'
                          ELSE now() - interval '1 minute' END AS upd,
                     CASE WHEN g <= {work_failed} THEN 1 ELSE 0 END AS att
              FROM generate_series(1, {c.work_items}) g
            ) s JOIN people p ON p.n = 1 + s.g % {c.people}""",
        ),
        (
            "outbox rows",
            f"""INSERT INTO core.outbox_rows (id, org_id, created_at, kind, target_id, payload,
                actor_id, request_id, app,
                                          done_at, attempts, failed_at, last_error)
            SELECT uuidv7(-(interval '8 days') + g * (interval '8 days' / {c.outbox_rows})),
                   CASE WHEN g % 2 = 0 THEN {big} ELSE p.org_id END,
                   CASE WHEN g > {outbox_done} AND g <= {outbox_pending} THEN now() - interval
                       '2 minutes'
                        ELSE now() - interval '8 days' + g * (interval '8 days' /
                            {c.outbox_rows}) END,
                   'tasks.task.updated', gen_random_uuid(), '{{}}', gen_random_uuid(),
                       gen_random_uuid(), 'portal',
                   CASE WHEN g <= {outbox_done} THEN now() - interval '8 days' + g * (interval
                       '8 days' / {c.outbox_rows}) END,
                   CASE WHEN g > {outbox_pending} THEN 10 ELSE 0 END,
                   CASE WHEN g > {outbox_pending} THEN now() - interval '1 day' END,
                   CASE WHEN g > {outbox_pending} THEN 'boom' END
            FROM generate_series(1, {c.outbox_rows}) g JOIN people p ON p.n = 1 + g % {c.people}""",
        ),
        (
            "cleanup records",
            f"""INSERT INTO core.orchestrations (id, org_id, created_at, updated_at, created_by,
                updated_by, kind, input,
                                             period, status, cursor, applied, skipped,
                                                 row_errors, version)
            SELECT gen_random_uuid(), {big}, now() - d * interval '1 day',
                now() - d * interval '1 day', '{EMPTY}',
                   '{EMPTY}', 'task_cleanup', '{{}}', to_char(current_date - d, 'YYYY-MM-DD'),
                       'succeeded', 0, 0, 0,
                   '[]', 3
            FROM generate_series(0, 20) d""",
        ),
        ("analyze", "ANALYZE"),
    ]


async def seed(name: str, scale: float) -> None:
    counts = Counts.at(scale)
    engine = create_async_engine(superuser_on(check_name(name)))
    try:
        async with engine.begin() as connection:
            for label, sql in statements(counts):
                started = time.perf_counter()
                await connection.exec_driver_sql(sql)
                print(f"{label}: {time.perf_counter() - started:.1f} s", flush=True)
    finally:
        await engine.dispose()
    print(f"{name}: seeded at scale {scale}: {counts}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="seed", description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("name", help="the audit database, audit_<slug>")
    parser.add_argument(
        "--scale", type=float, default=1.0, help="1 is the heavy seed; 0.01 a quick run"
    )
    args = parser.parse_args(argv)
    asyncio.run(seed(args.name, args.scale))
    return 0


if __name__ == "__main__":
    sys.exit(main())
