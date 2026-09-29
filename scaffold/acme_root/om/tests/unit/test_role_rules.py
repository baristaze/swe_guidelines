"""The role ladder is held to the permission table: a role at most another
holds a subset of its permissions, and the role reserved for services is
not a rung, so no comparison places it."""

import itertools

import pytest

from acme.om.opcontext import OperatorPermission, OperatorRole, Role
from acme.om.tenancy.rules import (
    OPERATOR_ROLE_PERMISSIONS,
    PERSON_ROLES,
    ROLE_PERMISSIONS,
    ROLE_RANK,
    capped_role,
    operator_permissions_of,
    permissions_of,
    role_at_most,
)


def test_the_ladder_holds_the_person_roles_and_only_them() -> None:
    assert set(ROLE_RANK) == set(Role) - {Role.SERVICE}
    assert set(PERSON_ROLES) == set(ROLE_RANK)
    assert set(ROLE_PERMISSIONS) == set(Role)


@pytest.mark.parametrize("requested,ceiling", list(itertools.product(PERSON_ROLES, repeat=2)))
def test_at_most_means_a_subset_of_permissions(requested: Role, ceiling: Role) -> None:
    if role_at_most(requested, ceiling):
        assert set(permissions_of(requested)) <= set(permissions_of(ceiling))
        assert capped_role(requested, ceiling) is requested
    else:
        assert capped_role(requested, ceiling) is ceiling


@pytest.mark.parametrize("role", PERSON_ROLES)
def test_every_role_is_at_most_itself(role: Role) -> None:
    assert role_at_most(role, role)


@pytest.mark.parametrize("other", PERSON_ROLES)
def test_the_service_role_is_no_rung(other: Role) -> None:
    # Apart from the ladder: at most no person role, no person role at most it.
    assert not role_at_most(Role.SERVICE, other)
    assert not role_at_most(other, Role.SERVICE)
    assert capped_role(Role.SERVICE, other) is other
    with pytest.raises(ValueError):
        capped_role(other, Role.SERVICE)
    assert not role_at_most(Role.SERVICE, Role.SERVICE)


def test_the_operator_table_holds_every_operator_role_and_write_includes_read() -> None:
    assert set(OPERATOR_ROLE_PERMISSIONS) == set(OperatorRole)
    assert operator_permissions_of(OperatorRole.READ) == {OperatorPermission.READ}
    assert operator_permissions_of(OperatorRole.READ) < operator_permissions_of(OperatorRole.WRITE)
    assert operator_permissions_of(OperatorRole.WRITE) == {
        OperatorPermission.READ,
        OperatorPermission.WRITE,
    }
    # ENROL is no entry's: the gate grants it alone, to an operator whose
    # second factor is not enrolled yet.
    assert all(
        OperatorPermission.ENROL not in operator_permissions_of(role) for role in OperatorRole
    )
