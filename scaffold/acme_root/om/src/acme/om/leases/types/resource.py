"""A resource: one thing of an org that one holder may use at a time, such
as a loading dock. It stands for a row of another namespace, by a registered
kind and that row's id, and it carries the lease store's anchor: the highest
token granted on it, the lease that holds it, and until when. The anchor's
row is what a grant locks."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, ClassVar
from uuid import UUID

from pydantic import Field, StringConstraints

from acme.om.base import Identifiable, Platform, Trackable

Label = Annotated[
    str, StringConstraints(min_length=1, max_length=200, pattern=r"^[^\x00-\x1f\x7f]+$")
]
"""One thing a resource offers, and a selector needs: free text of up to 200
characters, such as `cold` or `North door, bay 3`, matched as written."""

MAX_LABELS = 160
"""The most labels a resource offers, and a selector needs."""

MAX_TERM_SECONDS = 604_800
"""The longest bound a resource may set on one lease: seven days."""


class ResourceKind(StrEnum):
    """A product adds its kinds here, each with the shape of what its ask
    carries (`ASK_PAYLOADS`) and its hooks (`ResourceKindInterface`)."""

    NOOP = "noop"  # the mechanism's own: a grant starts nothing, and every request may be granted


class Resource(Identifiable, Trackable):
    MANAGER_OWNED_FIELDS: ClassVar[tuple[str, ...]] = (
        "available",
        "retired_at",
        "token",
        "lease_id",
        "held_until",
        "mean_hold_seconds",
    )
    """The kind and the row are the owner's at the registration, and the
    labels and the bound at the registration and at each update of its row
    (`ResourceUpdate`), which may name its availability too; the rest moves
    only through the manager's transitions."""

    kind: ResourceKind
    # The row it stands for, in the namespace that owns the kind. One
    # resource per org, kind, and row.
    ref_id: UUID
    labels: tuple[Label, ...] = Field(default=(), max_length=MAX_LABELS)
    # The bound on one lease: no grant and no renewal runs longer than this.
    max_term_seconds: int = Field(default=300, ge=1, le=MAX_TERM_SECONDS)
    # Out of service: held leases run on, and nothing new is granted.
    available: bool = True
    retired_at: datetime | None = None
    # The anchor. `token` is the highest granted, and the next grant takes
    # one more; `lease_id` and `held_until` name the lease that holds it.
    token: int = 0
    lease_id: UUID | None = None
    held_until: datetime | None = None
    # How long a lease of it is held, measured as each one ends; the wait
    # estimate replays its line from it.
    mean_hold_seconds: float | None = None


class ResourceUpdate(Platform):
    """An owner's update of its resource, landed with its own row: what the
    resource offers now, its bound on one lease, and whether it is in
    service. Each field it names changes, and each it leaves out (None)
    stays as it is, so taking a resource out of service names its
    availability alone. A lease it holds keeps its term, and a new bound
    holds from its next renewal; a request its labels no longer match
    leaves its line, and one they now match joins it."""

    labels: tuple[Label, ...] | None = Field(default=None, max_length=MAX_LABELS)
    max_term_seconds: int | None = Field(default=None, ge=1, le=MAX_TERM_SECONDS)
    available: bool | None = None

    def named(self) -> dict[str, object]:
        """The fields the update names, as the resource's row takes them."""
        return self.model_dump(exclude_none=True)
