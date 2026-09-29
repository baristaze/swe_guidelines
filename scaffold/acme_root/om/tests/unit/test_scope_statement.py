"""The scope the funnel sends in the message that begins a transaction.

A message that carries two statements takes no bind parameter, so the values
are written into the statement. These cases hold what may reach it: a UUID in
its canonical text, and nothing else.
"""

from uuid import UUID, uuid4

import pytest
from sqlalchemy.dialects.postgresql.asyncpg import AsyncAdapt_asyncpg_connection

from acme.om.base import EMPTY_UUID
from acme.om.storage.impl.pg_base import ScopedConnection, scope_statement
from acme.om.storage.impl.postgres import connect_args
from acme.om.storage.settings import RolePool


class _Loud(UUID):
    """A UUID whose text is not its value."""

    def __str__(self) -> str:
        return "x', true), set_config('app.org_id', '00000000-0000-0000-0000-000000000000"


def test_the_statement_sets_what_the_call_names_and_nothing_else() -> None:
    org_id, user_id, identity_id = uuid4(), uuid4(), uuid4()
    assert scope_statement(org_id, None, None) == (
        f"SELECT set_config('app.org_id', '{org_id}', true)"
    )
    assert scope_statement(org_id, user_id, identity_id) == (
        f"SELECT set_config('app.org_id', '{org_id}', true),"
        f" set_config('app.user_id', '{user_id}', true),"
        f" set_config('app.identity_id', '{identity_id}', true)"
    )


def test_the_system_scope_is_spelled_like_any_other() -> None:
    assert scope_statement(EMPTY_UUID, None, None) == (
        f"SELECT set_config('app.org_id', '{EMPTY_UUID}', true)"
    )


def test_a_value_that_is_not_a_uuid_never_reaches_the_statement() -> None:
    with pytest.raises(TypeError):
        scope_statement("00000000-0000-0000-0000-000000000000", None, None)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        scope_statement(uuid4(), "'; DROP TABLE core.orgs; --", None)  # type: ignore[arg-type]


def test_a_uuid_whose_text_is_not_canonical_never_reaches_the_statement() -> None:
    with pytest.raises(ValueError, match="canonical"):
        scope_statement(_Loud(int=uuid4().int), None, None)


def test_every_pooled_connection_can_carry_the_scope() -> None:
    args = connect_args(RolePool(size=1, checkout_timeout_seconds=1, statement_timeout_seconds=1))
    assert args["connection_class"] is ScopedConnection


def test_the_adapter_still_begins_through_the_method_the_funnel_calls() -> None:
    """The funnel begins the adapter's transaction itself, through a private
    method of SQLAlchemy's asyncpg adapter; an upgrade that moves it fails
    here."""
    assert callable(getattr(AsyncAdapt_asyncpg_connection, "_start_transaction", None))
