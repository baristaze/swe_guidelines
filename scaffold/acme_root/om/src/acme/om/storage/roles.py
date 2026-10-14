"""Every table belongs to exactly one database role; this map is the single
source of truth for the schema, the pool, and the migration chain."""

from enum import StrEnum


class DatabaseRole(StrEnum):
    CORE = "core"
    ACTIVITY = "activity"
    QUEUE = "queue"
    ADMIN = "admin"


TABLE_ROLES: dict[str, DatabaseRole] = {
    "api_keys": DatabaseRole.CORE,
    "auth_sessions": DatabaseRole.CORE,
    "change_event_cursors": DatabaseRole.ACTIVITY,
    "change_events": DatabaseRole.ACTIVITY,
    "files": DatabaseRole.CORE,
    "idempotency_records": DatabaseRole.CORE,
    "identities": DatabaseRole.CORE,
    "invitations": DatabaseRole.CORE,
    "leasable_resources": DatabaseRole.CORE,
    "lease_requests": DatabaseRole.CORE,
    "leases": DatabaseRole.CORE,
    "memberships": DatabaseRole.CORE,
    "orchestrations": DatabaseRole.CORE,
    "orgs": DatabaseRole.CORE,
    "outbox_rows": DatabaseRole.CORE,
    "sign_in_delays": DatabaseRole.CORE,
    "socket_tickets": DatabaseRole.CORE,
    "system_sizes": DatabaseRole.ADMIN,
    "tenant_caps": DatabaseRole.QUEUE,
    "users": DatabaseRole.CORE,
    "work_items": DatabaseRole.QUEUE,
    # A copy adds its entries below this line.
}

DROPPED_TABLE_ROLES: dict[str, DatabaseRole] = {
    "event_cursors": DatabaseRole.ACTIVITY,
    "events": DatabaseRole.ACTIVITY,
    "platform_sizes": DatabaseRole.ADMIN,
    "resources": DatabaseRole.CORE,
    "sessions": DatabaseRole.CORE,
    # A copy adds its entries below this line.
}
"""Tables the migration chain made and later dropped or renamed, by the name
they had. No process reaches them, so `role_for` does not know them; only the
chain names them, and its role check reads this map beside the live one."""


def role_for(table_name: str) -> DatabaseRole:
    try:
        return TABLE_ROLES[table_name]
    except KeyError:
        raise LookupError(
            f"table {table_name!r} has no database role; add it to TABLE_ROLES"
        ) from None
