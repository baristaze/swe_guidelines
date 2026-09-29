"""The pure rules: telling a change, reading a size, choosing an org."""

from uuid import UUID, uuid4

import pytest

from acme.apps.cli.model import choose_org, describe, human_size, org_lines
from acme.client.types import MembershipChoiceView

TARGET = UUID("0199a4c0-0000-7000-8000-0000000000dd")


def test_a_change_names_the_actor_the_action_and_the_record() -> None:
    assert describe("tenancy.invitation.created", TARGET, "Ann") == (
        f"Ann created tenancy.invitation {TARGET}"
    )
    assert describe("tenancy.api_key.deleted", TARGET, "Bob") == (
        f"Bob deleted tenancy.api_key {TARGET}"
    )
    assert describe("media.file.created", TARGET, "someone") == (
        f"someone created media.file {TARGET}"
    )


@pytest.mark.parametrize("kind", ["created", "tenancy.created", ".file.created", "media.file."])
def test_a_kind_of_another_shape_is_told_as_it_is(kind: str) -> None:
    assert describe(kind, TARGET, "Ann") == f"Ann: {kind} {TARGET}"


def test_a_size_reads_in_the_unit_a_person_reads() -> None:
    assert [human_size(n) for n in (0, 1023, 1024, 1536, 25 * 1024 * 1024)] == [
        "0 B",
        "1023 B",
        "1.0 KB",
        "1.5 KB",
        "25.0 MB",
    ]


def membership(
    slug: str, name: str, role: str = "member", kind: str = "team"
) -> MembershipChoiceView:
    return MembershipChoiceView.model_validate(
        {
            "org": {
                "id": str(uuid4()),
                "name": name,
                "slug": slug,
                "kind": kind,
                "created_at": "2026-09-18T12:00:00Z",
            },
            "user": {
                "id": str(uuid4()),
                "email": "ann@example.test",
                "display_name": "Ann",
                "created_at": "2026-09-18T12:00:00Z",
            },
            "role": role,
        }
    )


def test_choose_org_takes_the_slug_or_the_only_one() -> None:
    ajax, beta = membership("ajax", "Ajax"), membership("beta", "Beta")
    assert choose_org([ajax], None) == ajax
    assert choose_org([ajax, beta], "beta") == beta
    with pytest.raises(LookupError, match=r"^ajax, beta$"):
        choose_org([beta, ajax], None)
    with pytest.raises(LookupError, match=r"^ajax$"):
        choose_org([ajax], "nope")
    with pytest.raises(LookupError, match=r"^none$"):
        choose_org([], None)


def test_choose_org_takes_the_personal_org_when_none_is_named() -> None:
    ajax, mine = membership("ajax", "Ajax"), membership("ann-1x2y", "Ann", "owner", "personal")
    assert choose_org([ajax, mine], None) == mine
    assert choose_org([ajax, mine], "ajax") == ajax


def test_org_lines_sort_by_name_and_mark_the_current_org() -> None:
    lines = org_lines([membership("z-team", "Zeta"), membership("ajax", "Ajax", "owner")], "z-team")
    assert lines == "  ajax    Ajax (owner)\n* z-team  Zeta (member)"
    mine = org_lines([membership("ann-1x2y", "Ann", "owner", "personal")], None)
    assert mine == "  ann-1x2y  Ann (owner, personal)"
