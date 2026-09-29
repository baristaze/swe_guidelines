"""The infra family of exceptions, rooted here and not in the object model,
because infra imports nothing from it (ADR 0005). The root carries the same status and
stable code the platform's root does, so a boundary presents both alike. A
driver's own error type never crosses the infra boundary: every impl
translates it into one of these."""


class InfraException(Exception):
    """Root of every exception infra raises."""

    http_status: int = 500
    code: str = "infra_error"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.code)

    @property
    def message(self) -> str:
        return str(self.args[0]) if self.args else self.code


class InfraNotFound(InfraException):
    http_status = 404
    code = "not_found"


class InfraValidationFailed(InfraException):
    http_status = 422
    code = "validation_failed"


class InfraUnavailable(InfraException):
    """Not right now, on the infra side: the mirror of the platform's
    `Unavailable`. A leaf under it names what happened for the log and keeps
    this code, so an open breaker, a refused admission, and a backend that is
    down reach a caller as one code."""

    http_status = 503
    code = "unavailable"


class BackendFailed(InfraException):
    """The hosted backend answered with an error the impl cannot map to a
    shape. The message names the backend, the operation, and the backend's
    error code; never a payload or a secret value."""

    code = "backend_failed"

    def __init__(self, backend: str, operation: str, error_code: str) -> None:
        super().__init__(f"{backend} {operation} failed with {error_code}")


class BackendUnreachable(InfraUnavailable):
    """The hosted backend could not be reached or did not answer in time:
    the endpoint, the connection, or the read timed out or dropped. The
    message names the backend, the operation, and the driver's error class."""

    def __init__(self, backend: str, operation: str, reason: str) -> None:
        super().__init__(f"{backend} {operation} could not reach the backend: {reason}")


class BlobNotFound(InfraNotFound):
    code = "blob_not_found"


class InvalidBucketKey(InfraValidationFailed):
    code = "invalid_bucket_key"


class UploadRefused(InfraValidationFailed):
    """An upload outside the bounds it was presigned with: another content
    type, or a body larger than `max_bytes`."""

    code = "upload_refused"


class InvalidSecretName(InfraValidationFailed):
    code = "invalid_secret_name"


class SecretNotFound(InfraNotFound):
    code = "secret_not_found"

    def __init__(self, name: str, store: str) -> None:
        super().__init__(f"secret {name!r} not found in {store}")


class SecretsFileNotPrivate(InfraException):
    code = "secrets_file_not_private"


class PayloadMismatch(InfraValidationFailed):
    """A payload of the wrong type was offered to a topic."""

    code = "payload_mismatch"
