"""Composes one service impl per hosted namespace over the managers and
hands back the network-layer root the container holds."""

from datetime import timedelta

from acme.infra.root import InfraInterface
from acme.integrations.root import IntegrationsInterface
from acme.om.root import Managers
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
from acme.services.api.services.impl.admin import AdminServiceImpl
from acme.services.api.services.impl.change_events import ChangeEventsServiceImpl
from acme.services.api.services.impl.flags import FlagsServiceImpl
from acme.services.api.services.impl.leases import LeasesServiceImpl
from acme.services.api.services.impl.media import MediaServiceImpl
from acme.services.api.services.impl.realtime import RealtimeServiceImpl
from acme.services.api.services.impl.tenancy import TenancyServiceImpl
from acme.services.api.services.impl.webhooks import WebhooksServiceImpl


class ServicesImpl(ServicesInterface):
    def __init__(
        self,
        tenancy: TenancyServiceInterface,
        admin: AdminServiceInterface,
        change_events: ChangeEventsServiceInterface,
        media: MediaServiceInterface,
        flags: FlagsServiceInterface,
        realtime: RealtimeServiceInterface,
        webhooks: WebhooksServiceInterface,
        leases: LeasesServiceInterface,
    ) -> None:
        self._tenancy = tenancy
        self._admin = admin
        self._change_events = change_events
        self._media = media
        self._flags = flags
        self._realtime = realtime
        self._webhooks = webhooks
        self._leases = leases

    def get_tenancy_service(self) -> TenancyServiceInterface:
        return self._tenancy

    def get_admin_service(self) -> AdminServiceInterface:
        return self._admin

    def get_change_events_service(self) -> ChangeEventsServiceInterface:
        return self._change_events

    def get_media_service(self) -> MediaServiceInterface:
        return self._media

    def get_flags_service(self) -> FlagsServiceInterface:
        return self._flags

    def get_realtime_service(self) -> RealtimeServiceInterface:
        return self._realtime

    def get_webhooks_service(self) -> WebhooksServiceInterface:
        return self._webhooks

    def get_lease_service(self) -> LeasesServiceInterface:
        return self._leases


def build_services(
    managers: Managers,
    infra: InfraInterface,
    integrations: IntegrationsInterface,
    head_max_age: timedelta,
) -> ServicesInterface:
    """In-process impls only: a Python caller outside the process reaches the
    same services through the typed client under `clients/python`."""
    return ServicesImpl(
        tenancy=TenancyServiceImpl(managers.tenancy),
        admin=AdminServiceImpl(managers.tenancy_operator, managers.work_operator),
        change_events=ChangeEventsServiceImpl(managers.change_events),
        media=MediaServiceImpl(managers.media),
        flags=FlagsServiceImpl(infra.get_flags()),
        realtime=RealtimeServiceImpl(
            managers.tenancy, managers.change_events, infra.get_topics(), head_max_age
        ),
        webhooks=WebhooksServiceImpl(integrations.get_identity_provider(), infra.get_queues()),
        leases=LeasesServiceImpl(managers.leases),
    )
