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


class DeliveryRefused(InfraException):
    """An inbound delivery whose signature, timestamp, or body did not check
    out. Nothing is queued for it."""

    http_status = 400
    code = "webhook_signature_invalid"
