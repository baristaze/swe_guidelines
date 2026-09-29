"""Wire types of the provider webhook ingress."""

from acme.services.api.types.common import View


class DeliveryReceivedView(View):
    """The delivery checked out and is queued; the provider stops retrying."""

    received: bool
