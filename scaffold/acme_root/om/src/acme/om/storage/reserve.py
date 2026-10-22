"""Which call may take a pool's reserve: a holder's call, and nothing else.

A holder keeps what it holds only while its calls get through, so the gateway
marks a holder's call where it admits it, and every connection the call checks
out may come from the reserve beside a full pool (`impl/pg_base.py`,
`Reserve`). The mark has to reach every statement the call runs: its
authentication, its reads, its write, and a copy's own heartbeat or result,
on any role and under either login. So it rides the call as a context
variable, set around the whole of the call, rather than as an argument that
every storage method would have to carry and any one of them could drop.

This is the one piece of ambient state beside the request id's, and the same
boundary holds it. It is set only by the gateway's admission, around the call
it admitted in the holds lane, and reset in a `finally`, so it never outlives
the call. It is read only by the storage funnel, and only to choose which
connections serve a statement. It decides nothing a call returns: a call
served from the reserve reads and writes what it would from the pool, and a
call without the mark is served from the pool alone. The authoritative lane
stays on the request, where admission wrote it."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

holder_call_var: ContextVar[bool] = ContextVar("acme_holder_call", default=False)


@contextmanager
def as_holder_call() -> Iterator[None]:
    """Marks everything run inside as a holder's call, until it ends."""
    token = holder_call_var.set(True)
    try:
        yield
    finally:
        holder_call_var.reset(token)


def in_holder_call() -> bool:
    """Whether the statement about to run belongs to a holder's call."""
    return holder_call_var.get()
