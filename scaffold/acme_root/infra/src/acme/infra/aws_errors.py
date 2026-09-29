"""The one place botocore's error types are named. Every AWS impl runs its
calls inside `translated()` and catches `ClientError` only through this
module, so a caller only ever sees a platform exception: a service answer
it cannot map is `BackendFailed`, a backend it could not reach or that did
not answer in time is `BackendUnreachable`, and any other driver error is
`BackendFailed` under the driver's error class."""

import contextlib
from collections.abc import Iterator

from botocore.exceptions import BotoCoreError, ClientError, HTTPClientError
from botocore.exceptions import ConnectionError as BotoConnectionError

from acme.infra.exceptions import BackendFailed, BackendUnreachable

__all__ = ["ClientError", "error_code", "translated"]

UNREACHABLE = (BotoConnectionError, HTTPClientError)
"""Endpoint and connect failures, and the read and connect timeouts."""


def error_code(error: ClientError) -> str:
    return str(error.response.get("Error", {}).get("Code", "Unknown"))


@contextlib.contextmanager
def translated(backend: str, operation: str) -> Iterator[None]:
    """Turns any driver error that escapes the block into an infra exception.
    A block that maps a code to its own shape (a NotFound) does so inside."""
    try:
        yield
    except ClientError as error:
        raise BackendFailed(backend, operation, error_code(error)) from error
    except UNREACHABLE as error:
        raise BackendUnreachable(backend, operation, type(error).__name__) from error
    except BotoCoreError as error:
        raise BackendFailed(backend, operation, type(error).__name__) from error
