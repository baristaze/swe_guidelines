"""One router module per hosted namespace, mounted under /v1 by the app.

`all_routers(namespaces)` answers the routers of the namespaces named, or of
every hosted one when none is named. That is the first form of a split: the
same image, told by `ACME_NAMESPACES` which routers to mount, serves one
namespace as a service of its own, and no code changes."""

from collections.abc import Sequence

from fastapi import APIRouter

from acme.services.api.realtime import socket
from acme.services.api.routers import (
    admin,
    billing,
    events,
    imports,
    media,
    slack,
    tasks,
    tenancy,
)

HOSTED: dict[str, tuple[APIRouter, ...]] = {
    # The operator plane is tenancy's: it lists and deletes orgs.
    "tenancy": (tenancy.router, admin.router),
    # The imports first: `/tasks/imports` is not a task's id.
    "tasks": (imports.router, tasks.router),
    # The realtime channel is the events stream pushed; its replay is `/events`.
    "events": (events.router, socket.router),
    "media": (media.router,),
    # The org's plan, its checkout, and its portal at the processor.
    "billing": (billing.router,),
    # A tenant's Slack connection, and the endpoint Slack itself calls.
    "slack": (slack.router,),
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
