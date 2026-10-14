"""The kinds of work are a registry the root builds: each kind once, named
in capitals, the work namespace's own among them, and a row that names a
kind holds it to the same pattern."""

import pytest
from pydantic import ValidationError

from acme.om.context import Permission
from acme.om.root import KINDS
from acme.om.work.kinds import WORK_KINDS, WorkKinds, WorkKindSpec
from acme.om.work.types.work_item import NoopPayload, WorkItem, WorkKind

from contracts.work_storage import make_item

LONG = WorkKindSpec("LONG", NoopPayload, Permission.WRITE, lane="long")


def test_the_work_namespaces_specs_name_its_own_kinds_and_the_root_registers_them() -> None:
    assert [kind.name for kind in WORK_KINDS] == sorted(WorkKind)
    assert all(kind.name in KINDS for kind in WORK_KINDS)
    assert all(kind.lane == "default" for kind in WORK_KINDS)


def test_a_kind_is_named_in_capitals() -> None:
    for name in ("noop", "Long", "1ST", "", "A" * 65, "LONG\n"):
        with pytest.raises(ValueError, match="named in capitals"):
            WorkKindSpec(name, NoopPayload, Permission.WRITE)


def test_a_name_registered_twice_is_refused_so_no_kind_is_taken_over() -> None:
    noop = WorkKindSpec(WorkKind.NOOP, NoopPayload, Permission.READ)
    with pytest.raises(ValueError, match="work kind NOOP is registered twice"):
        KINDS.extended((noop,))
    with pytest.raises(ValueError, match="work kind LONG is registered twice"):
        WorkKinds((LONG, LONG))


def test_an_extended_registry_holds_both_and_leaves_the_first_as_it_was() -> None:
    extended = KINDS.extended((LONG,))
    assert extended.get("LONG") is LONG and "LONG" in extended
    assert "LONG" not in KINDS and KINDS.get("LONG") is None
    assert [kind.name for kind in extended] == [*(kind.name for kind in KINDS), "LONG"]


def test_a_row_holds_its_kind_to_the_pattern() -> None:
    row = make_item().model_dump()
    assert WorkItem.model_validate({**row, "kind": "LONG"}).kind == "LONG"
    with pytest.raises(ValidationError):
        WorkItem.model_validate({**row, "kind": "long"})
