"""The kinds of long-running records, as a registry. A kind is a name and the
shape of the input its records work on; the step the worker runs for it is
keyed by the same name in the worker. The mechanism's own kind is
`NOOP_ORCHESTRATION`; a copy declares its own in its namespace's `kinds.py`.
The business root builds the one registry a process holds from both
(`root.ORCHESTRATION_KINDS`), and the orchestrations manager holds every
start to it, so no kind is a line in a list the scaffold also writes."""

import re
from dataclasses import dataclass

from acme.om.base import SNAKE_NAME, Kinds, OmBase
from acme.om.orchestrations.types.orchestration import NoopInput, OrchestrationKind


@dataclass(frozen=True)
class OrchestrationKindSpec:
    """One kind of long-running record. `input` is the shape every record of
    it works on, which the start holds it to. A kind a person starts says,
    in the namespace that starts it, what a second start does while one is
    open: it answers the open record, it is refused, or it starts beside
    it."""

    name: str
    input: type[OmBase]

    def __post_init__(self) -> None:
        if not re.fullmatch(SNAKE_NAME, self.name):
            raise ValueError(f"an orchestration kind is named in snake case, never {self.name!r}")


class OrchestrationKinds(Kinds[OrchestrationKindSpec]):
    """The kinds of long-running record a process knows, each once (`Kinds`)."""

    NOUN = "orchestration kind"


NOOP_ORCHESTRATION = OrchestrationKindSpec(OrchestrationKind.NOOP, NoopInput)
"""The mechanism's own kind."""
