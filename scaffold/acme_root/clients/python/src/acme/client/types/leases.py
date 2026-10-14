"""The leases namespace's types: a leasable resource, its wait line, and its
leases."""

from acme.client.schema.leases import (
    EndReason,
    LeasableResourceKind,
    LeasableResourceView,
    LeaseEntryView,
    LeasePageView,
    LeaseRequestView,
    LeaseStatus,
    LeaseView,
    PlaceView,
    RequestStatus,
    StandingView,
    WaiterKind,
    WaitLineView,
)

__all__ = [
    "EndReason",
    "LeasableResourceKind",
    "LeasableResourceView",
    "LeaseEntryView",
    "LeasePageView",
    "LeaseRequestView",
    "LeaseStatus",
    "LeaseView",
    "PlaceView",
    "RequestStatus",
    "StandingView",
    "WaitLineView",
    "WaiterKind",
]
