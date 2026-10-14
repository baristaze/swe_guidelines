"""The kinds of work, as a registry. A kind is a name, the payload its items
carry, the permission that asks for it, and the lane its items land on. The
work namespace's own kinds are `WORK_KINDS`; a copy declares its own in its
namespace's `kinds.py`. The business root builds the one registry a process
holds from both (`root.KINDS`), and the work manager reads every kind
through it, so no kind is a line in a list the scaffold also writes."""

import re
from dataclasses import dataclass

from acme.om.base import Kinds, OmBase
from acme.om.context import Permission
from acme.om.work.types.work_item import (
    KIND_NAME,
    DeleteAccountPayload,
    DeleteOrgPayload,
    NoopPayload,
    OrchestrationPayload,
    WakeParkedPayload,
    WorkKind,
)


@dataclass(frozen=True)
class WorkKindSpec:
    """One kind of work. `payload` is the shape every item of it carries,
    which the enqueue holds it to. `permission` is what asks for it: the
    person who asks authorizes the whole run once, so it is as wide as the
    run, and every role that holds it holds every permission the kind's
    handler calls with, which the worker's tests hold each handler to.
    `lane` is where the relay lands its items: a long-held kind runs on a
    lane of its own, kept off the lane of short items and that lane's
    tenant cap. A worker of the kind's own reads its lane here too, so the
    two cannot disagree; a replica of a worker that serves many kinds is
    deployed on the lane spelled here."""

    name: str
    payload: type[OmBase]
    permission: Permission
    lane: str = "default"

    def __post_init__(self) -> None:
        if not re.fullmatch(KIND_NAME, self.name):
            raise ValueError(f"a work kind is named in capitals, never {self.name!r}")


class WorkKinds(Kinds[WorkKindSpec]):
    """The kinds of work a process knows, each once (`Kinds`)."""

    NOUN = "work kind"


WORK_KINDS: tuple[WorkKindSpec, ...] = (
    # Only an account's deletion asks for this one, relayed from its own
    # commit: leaving is every person's right whatever their role, so no
    # route enqueues it, and the permission is the width of the handler.
    WorkKindSpec(WorkKind.DELETE_ACCOUNT, DeleteAccountPayload, Permission.MANAGE_MEMBERS),
    # Only the deletion of a team org, an owner's or an operator's, asks for
    # this one, relayed from its own commit; no route enqueues it.
    WorkKindSpec(WorkKind.DELETE_ORG, DeleteOrgPayload, Permission.MANAGE_MEMBERS),
    WorkKindSpec(WorkKind.NOOP, NoopPayload, Permission.WRITE),
    WorkKindSpec(WorkKind.ORCHESTRATION, OrchestrationPayload, Permission.WRITE),
    WorkKindSpec(WorkKind.WAKE_PARKED, WakeParkedPayload, Permission.WRITE),
)
"""The work namespace's own kinds, each on the default lane."""
