from pydantic import Field

from acme.services.api.types.common import View


class FlagsView(View):
    """The session's flags that a client may read, evaluated for its org and
    its user. A flag read on the server alone is never in it."""

    flags: dict[str, bool] = Field(
        description="Each flag marked for clients, by name, and its value for the "
        "session's org and user."
    )
