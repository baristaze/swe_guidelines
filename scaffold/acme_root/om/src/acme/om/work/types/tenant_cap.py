"""A tenant's own cap on a lane: the most items the tenant holds claimed on
the lane under a live lease, in place of the lane's cap for that tenant. One
row per tenant and lane, set by an operator; with no row, the lane's cap
holds, or none."""

from typing import Annotated

from pydantic import Field, StringConstraints

from acme.om.base import Identifiable, Trackable

Lane = Annotated[str, StringConstraints(min_length=1, max_length=64)]
"""A lane's name, as a worker claims from it: `default`, or one a deployment
names."""

MAX_CAP = 10_000
"""Far above the workers any lane runs, and well inside the column's range."""


class TenantCap(Identifiable, Trackable):
    lane: Lane
    # At least one: a tenant whose work must not run on a lane is not a cap's
    # business, and a count never passes over a tenant holding nothing.
    cap: int = Field(ge=1, le=MAX_CAP)
