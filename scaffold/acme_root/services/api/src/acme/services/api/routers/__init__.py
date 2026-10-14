"""One router module per hosted namespace, mounted under /v1 by the app.

Each module is named by its namespace and tags every route with it, so the
namespace's OpenAPI document holds its routes alone. A module holds the
tenant's routes in `router` and, where the namespace has them, the operator
plane's in `operator`, under `/admin`. Its docstring describes its tag: the
namespace's routes by area, read in its document.

`all_routers(namespaces)` answers the routers of the namespaces named, or of
every hosted one when none is named. That is the first form of a split: the
same image, told by `ACME_NAMESPACES` which routers to mount, serves one
namespace as a service of its own, and no code changes."""

import inspect
from collections.abc import Sequence
from importlib import import_module

from fastapi import APIRouter

from acme.services.api.realtime import socket
from acme.services.api.routers import change_events, leases, media, tenancy, work

HOSTED: dict[str, tuple[APIRouter, ...]] = {
    # The operator plane is tenancy's: it lists and deletes orgs. The
    # auth session's flags are too: they answer for its org and user.
    "tenancy": (tenancy.router, tenancy.operator),
    # The realtime channel is the change events pushed; its replay is
    # `/change-events`.
    "change_events": (change_events.router, change_events.operator, socket.router),
    "media": (media.router,),
    "leases": (leases.router,),
    # The work queue has the operator's routes alone: a tenant's work is
    # queued by the writes of the other namespaces.
    "work": (work.operator,),
}
"""Every namespace this image hosts, and the routers that serve it."""


def all_routers(namespaces: Sequence[str] = ()) -> list[APIRouter]:
    """The routers to mount; a name this image does not host refuses the boot."""
    unknown = sorted(set(namespaces) - set(HOSTED))
    if unknown:
        raise ValueError(
            f"ACME_NAMESPACES names {', '.join(unknown)}; this image hosts {', '.join(HOSTED)}"
        )
    chosen = namespaces or tuple(HOSTED)
    return [router for name in HOSTED if name in chosen for router in HOSTED[name]]


def tags() -> list[dict[str, str]]:
    """Each hosted namespace's tag, described by its router module's docstring."""
    return [
        {"name": name, "description": inspect.getdoc(import_module(f"{__name__}.{name}")) or ""}
        for name in HOSTED
    ]
