"""What the storage funnel makes of a database that did not answer in time:
a checkout past its bound and a statement past its deadline are
`Unavailable`, counted under the bound that ended them; every other failure is
left as it is. The same cases over a live Postgres are in
`om/tests/integration/test_storage_bounds.py`."""

import pytest
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from acme.infra.observability import OUTCOMES
from acme.om.exceptions import Unavailable
from acme.om.storage.impl.pg_base import QUERY_CANCELED, not_in_time
from acme.om.storage.roles import DatabaseRole


class DriverError(Exception):
    """The driver's error, which names the SQLSTATE the server answered."""

    def __init__(self, sqlstate: str) -> None:
        super().__init__(f"sqlstate {sqlstate}")
        self.sqlstate = sqlstate


def adapted(driver: DriverError) -> DBAPIError:
    """What SQLAlchemy raises: its own error over the adapter's, which wraps
    the driver's as its cause."""
    adapter = Exception(str(driver))
    adapter.__cause__ = driver
    return DBAPIError("SELECT 1", None, adapter)


def counted(outcome: str) -> float:
    return OUTCOMES.labels(subsystem="storage", outcome=outcome)._value.get()


@pytest.mark.parametrize(
    "error",
    [
        PoolTimeoutError("QueuePool limit of size 1 overflow 0 reached"),
        TimeoutError(),  # the driver's own connect timeout
    ],
    ids=["pool", "connect"],
)
def test_a_checkout_past_its_bound_is_unavailable(error: Exception) -> None:
    before = counted("checkout_timeout")
    refusal = not_in_time(error, DatabaseRole.QUEUE, "system")
    assert isinstance(refusal, Unavailable)
    assert (refusal.http_status, refusal.code) == (503, "unavailable")
    assert refusal.message == (
        "the database did not answer in time: "
        "no connection to the queue role (system login) within the checkout bound"
    )
    assert counted("checkout_timeout") == before + 1


def test_a_statement_past_its_deadline_is_unavailable() -> None:
    before = counted("statement_timeout")
    refusal = not_in_time(adapted(DriverError(QUERY_CANCELED)), DatabaseRole.CORE, "runtime")
    assert isinstance(refusal, Unavailable)
    assert refusal.message == (
        "the database did not answer in time: "
        "a statement on the core role (runtime login) passed its deadline"
    )
    assert counted("statement_timeout") == before + 1


@pytest.mark.parametrize(
    "error",
    [
        adapted(DriverError("40P01")),  # a deadlock: the call itself lost
        IntegrityError("INSERT", None, DriverError("23505")),
        RuntimeError("anything else"),
        ConnectionRefusedError(),
    ],
    ids=["deadlock", "unique-key", "runtime", "refused"],
)
def test_any_other_failure_is_left_as_it_is(error: Exception) -> None:
    before = (counted("checkout_timeout"), counted("statement_timeout"))
    assert not_in_time(error, DatabaseRole.CORE, "runtime") is None
    assert (counted("checkout_timeout"), counted("statement_timeout")) == before
