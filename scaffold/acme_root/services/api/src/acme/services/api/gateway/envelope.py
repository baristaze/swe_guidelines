"""The one error envelope: `{"error": {"code", "message", "request_id"}}`,
rendered from the request id the middleware stamped. The exception handlers
and the middleware's own last resort both produce it from here."""

from uuid import UUID

from fastapi.responses import JSONResponse

from acme.services.api.types.common import (
    ErrorBody,
    ErrorResponse,
    LastOwnerDetail,
    StreamTruncatedDetail,
)

INTERNAL_ERROR = ("internal_error", "internal error")
"""The code and message a client sees for any failure it cannot act on."""


def error_response(
    request_id: UUID,
    status: int,
    code: str,
    message: str,
    headers: dict[str, str] | None = None,
    stream: StreamTruncatedDetail | None = None,
    last_owner: LastOwnerDetail | None = None,
) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorBody(
            code=code,
            message=message,
            request_id=request_id,
            stream=stream,
            last_owner=last_owner,
        )
    )
    # A detail the refusal does not carry is absent, not null; a detail it
    # carries keeps its own null fields.
    details = (("stream", stream), ("last_owner", last_owner))
    absent = {name for name, value in details if not value}
    content = body.model_dump(mode="json", exclude={"error": absent})
    return JSONResponse(status_code=status, content=content, headers=headers)
