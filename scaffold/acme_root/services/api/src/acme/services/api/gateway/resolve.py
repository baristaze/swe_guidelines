"""Resolves the one container, and the services it holds, for a request or a
socket. Routers name the service they need and make one call into it."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import Depends
from starlette.requests import HTTPConnection

from acme.services.api.services import (
    AdminServiceInterface,
    ChangeEventsServiceInterface,
    FlagsServiceInterface,
    LeasesServiceInterface,
    MediaServiceInterface,
    RealtimeServiceInterface,
    ServicesInterface,
    TenancyServiceInterface,
    WebhooksServiceInterface,
)

if TYPE_CHECKING:
    from acme.services.api.container import AppContainer


def container_of(connection: HTTPConnection) -> AppContainer:
    return connection.app.state.container


def services_of(connection: HTTPConnection) -> ServicesInterface:
    return container_of(connection).services


def admin_service(connection: HTTPConnection) -> AdminServiceInterface:
    return services_of(connection).get_admin_service()


AdminService = Annotated[AdminServiceInterface, Depends(admin_service)]


def change_events_service(connection: HTTPConnection) -> ChangeEventsServiceInterface:
    return services_of(connection).get_change_events_service()


ChangeEventsService = Annotated[ChangeEventsServiceInterface, Depends(change_events_service)]


def flags_service(connection: HTTPConnection) -> FlagsServiceInterface:
    return services_of(connection).get_flags_service()


FlagsService = Annotated[FlagsServiceInterface, Depends(flags_service)]


def lease_service(connection: HTTPConnection) -> LeasesServiceInterface:
    return services_of(connection).get_lease_service()


LeasesService = Annotated[LeasesServiceInterface, Depends(lease_service)]


def media_service(connection: HTTPConnection) -> MediaServiceInterface:
    return services_of(connection).get_media_service()


MediaService = Annotated[MediaServiceInterface, Depends(media_service)]


def realtime_service(connection: HTTPConnection) -> RealtimeServiceInterface:
    return services_of(connection).get_realtime_service()


RealtimeService = Annotated[RealtimeServiceInterface, Depends(realtime_service)]


def tenancy_service(connection: HTTPConnection) -> TenancyServiceInterface:
    return services_of(connection).get_tenancy_service()


TenancyService = Annotated[TenancyServiceInterface, Depends(tenancy_service)]


def webhooks_service(connection: HTTPConnection) -> WebhooksServiceInterface:
    return services_of(connection).get_webhooks_service()


WebhooksService = Annotated[WebhooksServiceInterface, Depends(webhooks_service)]


# A copy adds its entries below this line.
