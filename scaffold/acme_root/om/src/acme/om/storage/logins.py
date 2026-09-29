"""The three database logins, by name, and the one command that makes them.

None of the three is a superuser or carries BYPASSRLS, since either walks past
every row-level security policy and the policies are the second fence.

- The migration login owns every schema and table. It runs the migrations in
  the deploy's one-off task, and no process holds it otherwise.
- The runtime login is what every request's connection uses. It owns nothing
  and holds SELECT, INSERT, UPDATE, and DELETE, so it cannot drop a policy,
  turn FORCE off, or alter a table, whatever statement reaches it.
- The system login is the runtime login's twin for the system scope. The
  listed system-scope methods run under it, on a pool of their own, and the
  policies admit the system scope to it alone.

The policies name the logins, so the names are fixed here and a URL that
names another login is refused rather than followed.

`ensure_logins` runs as the master: the cloud's master user, or `acme`
locally. It is idempotent. A second run finds every login, schema, owner, and
grant already in place and changes nothing but the passwords, which it sets
from the URLs again.
"""

from collections.abc import Iterable

from sqlalchemy import Connection, text
from sqlalchemy.engine import URL, make_url

from acme.om.storage.roles import DatabaseRole

MIGRATION_LOGIN = "acme_migration"
RUNTIME_LOGIN = "acme_runtime"
SYSTEM_LOGIN = "acme_system"

DML = "SELECT, INSERT, UPDATE, DELETE"
VERSION_TABLE = "alembic_version"


def with_login(role_url: str, login_url: str) -> str:
    """The URL of a role's database under another login: the host, the port,
    and the database of `role_url`, with the user and the password of
    `login_url`. A role that moves to a database of its own names one URL, and
    its system and migration connections follow it there."""
    role, login = make_url(role_url), make_url(login_url)
    swapped: URL = role.set(username=login.username, password=login.password)
    return swapped.render_as_string(hide_password=False)


def login_of(url: str, expected: str) -> tuple[str, str]:
    """The login and the password a URL names, refused when the login is not
    the one the policies expect."""
    parsed = make_url(url)
    if parsed.username != expected:
        raise SystemExit(f"the URL for {expected} names the login {parsed.username!r}")
    if not parsed.password:
        raise SystemExit(f"the URL for {expected} carries no password")
    return expected, parsed.password


def _run(connection: Connection, statement: str) -> None:
    connection.exec_driver_sql(statement)


def _formatted(connection: Connection, template: str, *values: str) -> str:
    """A utility statement with its identifiers and literals quoted by the
    server's own `format()`: CREATE ROLE and GRANT take no bind parameters,
    and a password is never spliced into a statement by hand."""
    params = {f"v{i}": value for i, value in enumerate(values)}
    args = ", ".join(f"CAST(:v{i} AS text)" for i in range(len(values)))
    statement = text(f"SELECT format(CAST(:template AS text), {args})")
    return connection.execute(statement, {"template": template, **params}).scalar_one()


def ensure_login(connection: Connection, login: str, password: str) -> None:
    """Creates the login, or sets its password when it exists. The attributes
    are named on creation only: a master that is not a superuser may not name
    SUPERUSER or BYPASSRLS on an ALTER, even to turn them off. They are read
    back after, and a login that carries either is refused."""
    exists = connection.execute(
        text("SELECT 1 FROM pg_roles WHERE rolname = :login"), {"login": login}
    ).first()
    if exists is None:
        _run(
            connection,
            _formatted(
                connection,
                "CREATE ROLE %I LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE"
                " NOREPLICATION PASSWORD %L",
                login,
                password,
            ),
        )
    else:
        _run(connection, _formatted(connection, "ALTER ROLE %I LOGIN PASSWORD %L", login, password))
    found = connection.execute(
        text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = :login"),
        {"login": login},
    ).one()
    if found.rolsuper or found.rolbypassrls:
        raise SystemExit(
            f"{login} is a superuser or carries BYPASSRLS; the fence would be a drawing"
        )


def _grant_membership(connection: Connection, login: str) -> None:
    """The master becomes a member of the migration login, which is what lets
    it hand the migration login what it owns today and set its default
    privileges. Granted once; a second run finds the membership."""
    master = connection.execute(text("SELECT current_user")).scalar_one()
    member = connection.execute(
        text(
            "SELECT 1 FROM pg_auth_members m"
            " JOIN pg_roles r ON r.oid = m.roleid JOIN pg_roles u ON u.oid = m.member"
            " WHERE r.rolname = :login AND u.rolname = :master AND m.set_option"
        ),
        {"login": login, "master": master},
    ).first()
    if member is None:
        _run(
            connection,
            _formatted(connection, "GRANT %I TO %I WITH INHERIT TRUE, SET TRUE", login, master),
        )


def _owned_by_others(connection: Connection, schema: str) -> Iterable[tuple[str, str]]:
    """Every table, view, and free-standing sequence of a schema whose owner
    is not the migration login, as (kind, name). A sequence an identity or a
    serial column owns moves with its table and is not listed."""
    rows = connection.execute(
        text(
            "SELECT c.relkind::text AS relkind, c.relname FROM pg_class c"
            " JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE n.nspname = :schema AND c.relkind IN ('r', 'p', 'v', 'm', 'S')"
            " AND pg_get_userbyid(c.relowner) <> :owner"
            " AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid = c.oid"
            "   AND d.classid = 'pg_class'::regclass AND d.deptype IN ('a', 'i')"
            "   AND c.relkind = 'S')"
            " ORDER BY c.relname"
        ),
        {"schema": schema, "owner": MIGRATION_LOGIN},
    )
    kinds = {"r": "TABLE", "p": "TABLE", "v": "VIEW", "m": "MATERIALIZED VIEW", "S": "SEQUENCE"}
    return [(kinds[row.relkind], row.relname) for row in rows]


def _own_schema(connection: Connection, schema: str) -> None:
    """The schema exists and is the migration login's, with everything in it."""
    _run(
        connection,
        _formatted(
            connection, "CREATE SCHEMA IF NOT EXISTS %I AUTHORIZATION %I", schema, MIGRATION_LOGIN
        ),
    )
    owner = connection.execute(
        text("SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = :schema"),
        {"schema": schema},
    ).scalar_one()
    if owner != MIGRATION_LOGIN:
        _run(
            connection,
            _formatted(connection, "ALTER SCHEMA %I OWNER TO %I", schema, MIGRATION_LOGIN),
        )
    for kind, name in _owned_by_others(connection, schema):
        _run(
            connection,
            _formatted(
                connection, f"ALTER {kind} %I.%I OWNER TO %I", schema, name, MIGRATION_LOGIN
            ),
        )


def grant_statements(schema: str) -> list[str]:
    """What the runtime and the system logins hold on a schema: usage, DML on
    every table but the migration bookkeeping, the sequences, and the same on
    every table the migration login creates later. The grant migration of each
    chain runs the same statements, so a fresh database and a database brought
    under the logins end in one state."""
    grantees = f"{RUNTIME_LOGIN}, {SYSTEM_LOGIN}"
    return [
        f"GRANT USAGE ON SCHEMA {schema} TO {grantees}",
        f"GRANT {DML} ON ALL TABLES IN SCHEMA {schema} TO {grantees}",
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {schema} TO {grantees}",
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {MIGRATION_LOGIN} IN SCHEMA {schema}"
        f" GRANT {DML} ON TABLES TO {grantees}",
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {MIGRATION_LOGIN} IN SCHEMA {schema}"
        f" GRANT USAGE, SELECT ON SEQUENCES TO {grantees}",
    ]


def _grant(connection: Connection, schema: str) -> None:
    for statement in grant_statements(schema):
        _run(connection, statement)
    version = connection.execute(
        text("SELECT to_regclass(:table)"), {"table": f"{schema}.{VERSION_TABLE}"}
    ).scalar_one()
    if version is not None:
        _run(
            connection,
            f"REVOKE ALL ON {schema}.{VERSION_TABLE} FROM {RUNTIME_LOGIN}, {SYSTEM_LOGIN}",
        )


def ensure_logins(connection: Connection, passwords: dict[str, str]) -> None:
    """The whole command, in the caller's one transaction, as the master: the
    three logins with the passwords their URLs carry, the master a member of
    the migration login, every role schema and everything in it owned by the
    migration login, and the runtime and system logins granted DML now and on
    every table to come. `passwords` maps each of the three logins to its
    password."""
    database = connection.execute(text("SELECT current_database()")).scalar_one()
    for login in (MIGRATION_LOGIN, RUNTIME_LOGIN, SYSTEM_LOGIN):
        ensure_login(connection, login, passwords[login])
    _grant_membership(connection, MIGRATION_LOGIN)
    _run(
        connection,
        _formatted(
            connection,
            "GRANT CONNECT, CREATE ON DATABASE %I TO %I",
            database,
            MIGRATION_LOGIN,
        ),
    )
    for login in (RUNTIME_LOGIN, SYSTEM_LOGIN):
        _run(
            connection,
            _formatted(connection, "GRANT CONNECT ON DATABASE %I TO %I", database, login),
        )
    for role in DatabaseRole:
        _own_schema(connection, role.value)
        _grant(connection, role.value)
