"""What an integration raises. Every leaf is an infra exception, so a
boundary presents it the way it presents any other (ADR 0005); a provider's
own error type never crosses the integration boundary. The tenancy manager,
the one caller, translates the sign-in leaves into the platform's own."""

from acme.infra.exceptions import InfraException, InfraUnavailable


class UnsafeIntegration(InfraException):
    """A setting that is only safe locally, in a deployed environment: the
    twin. Refused at boot, naming the setting."""

    code = "unsafe_configuration"


class ProviderUnavailable(InfraUnavailable):
    """The provider did not answer, answered with a server error, or is not
    configured in this process."""


class ProviderRefused(InfraException):
    """The provider refused what it was handed: a code that is spent,
    expired, or never issued; an invitation it will not send; a link it will
    not make. The message is the provider's, for the log."""

    http_status = 400
    code = "provider_refused"


class ProviderConflict(ProviderRefused):
    """The provider holds something that stands in the way: an invitation
    already pending for the address, say."""

    http_status = 409
    code = "provider_conflict"


class DevicePending(InfraException):
    """The person has not confirmed the device sign-in yet; ask again after
    the interval."""

    http_status = 400
    code = "authorization_pending"


class DeviceSlowDown(DevicePending):
    """Asked too often; wait longer before the next ask."""

    code = "slow_down"


class DeviceDenied(ProviderRefused):
    """The person declined the device sign-in."""

    code = "access_denied"


class DeviceExpired(ProviderRefused):
    """The device code expired before the person confirmed it."""

    code = "expired_token"


class PaymentsUnconfigured(ProviderUnavailable):
    """This environment holds no credential for the payment processor. The
    plans still apply; nobody can buy one here."""

    code = "billing_unavailable"


class PaymentsKeyRefused(ProviderUnavailable):
    """The processor refused the process's own key: revoked (401), or
    without the permission the call needs (403). Nothing is wrong with the
    call; it goes through once a person fixes the key, so it is unavailable
    until then, not refused. The message names the operation and the
    processor's error code, never the key."""

    code = "payments_key_refused"

    def __init__(self, operation: str, reason: str) -> None:
        super().__init__(
            f"the payment processor refused the runtime key for {operation} ({reason}): "
            "give the key the permission, or replace it"
        )


class PaymentsRefused(InfraException):
    """The processor answered, and refused the request itself (a 4xx about
    what it was handed): a price it does not know, a card it declined, a
    request it calls invalid. The same request gets the same answer. The
    message names the operation and the processor's error code, never a
    payload or a key."""

    http_status = 502
    code = "payments_refused"

    def __init__(self, operation: str, reason: str) -> None:
        super().__init__(f"the payment processor refused {operation}: {reason}")


class DeliveryRefused(InfraException):
    """An inbound delivery whose signature, timestamp, or body did not check
    out. Nothing is queued for it."""

    http_status = 400
    code = "webhook_signature_invalid"
