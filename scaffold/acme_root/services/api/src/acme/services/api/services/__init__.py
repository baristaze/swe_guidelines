"""The network-layer root: one getter per service. A router resolves the
service it needs from this object and makes one call into it; the impl
translates the request, calls one manager, and projects the result."""

from abc import ABC, abstractmethod

from acme.services.api.services.admin import AdminServiceInterface
from acme.services.api.services.events import EventsServiceInterface
from acme.services.api.services.flags import FlagsServiceInterface
from acme.services.api.services.leases import LeasesServiceInterface
from acme.services.api.services.media import MediaServiceInterface
from acme.services.api.services.realtime import RealtimeServiceInterface
from acme.services.api.services.tenancy import TenancyServiceInterface
from acme.services.api.services.webhooks import WebhooksServiceInterface

__all__ = [
    "AdminServiceInterface",
    "EventsServiceInterface",
    "FlagsServiceInterface",
    "LeasesServiceInterface",
    "MediaServiceInterface",
    "RealtimeServiceInterface",
    "ServicesInterface",
    "TenancyServiceInterface",
    "WebhooksServiceInterface",
]


class ServicesInterface(ABC):
    @abstractmethod
    def get_tenancy_service(self) -> TenancyServiceInterface: ...

    @abstractmethod
    def get_admin_service(self) -> AdminServiceInterface: ...

    @abstractmethod
    def get_events_service(self) -> EventsServiceInterface: ...

    @abstractmethod
    def get_media_service(self) -> MediaServiceInterface: ...

    @abstractmethod
    def get_flags_service(self) -> FlagsServiceInterface: ...

    @abstractmethod
    def get_lease_service(self) -> LeasesServiceInterface: ...

    @abstractmethod
    def get_realtime_service(self) -> RealtimeServiceInterface: ...

    @abstractmethod
    def get_webhooks_service(self) -> WebhooksServiceInterface: ...
