"""The pure rules: describing a change, the mine filter, short ids."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from acme.apps.cli.model import (
    attachment_table,
    describe,
    human_size,
    is_mine,
    resolve,
    resolve_file,
    short_id,
    task_table,
)
from acme.client.types import FilePurpose, FileStatus, FileView, TaskStatus, TaskView

ME, BOB = uuid4(), uuid4()
NAMES = {ME: "Ann", BOB: "Bob"}


def name_of(user_id):
    return NAMES.get(user_id, "nobody")


def task(**changes) -> TaskView:
    base = {
        "id": uuid4(),
        "title": "Migrate DB",
        "notes": "",
        "status": TaskStatus.open,
        "assignee_id": None,
        "rank": "0",
        "created_at": "2026-09-18T12:00:00Z",
        "updated_at": "2026-09-18T12:00:00Z",
        "created_by": ME,
        "deleted_at": None,
        "version": 1,
    }
    return TaskView.model_validate({**base, **changes})


def test_created_and_deleted_name_the_actor_and_the_title() -> None:
    t = task()
    assert describe("created", None, t, "Bob", name_of) == "Bob created a task: Migrate DB"
    assert describe("deleted", t, None, "Bob", name_of) == "Bob deleted a task: Migrate DB"
    assert describe("deleted", None, None, "Bob", name_of) == "Bob deleted a task: a task"


def test_an_update_names_the_first_difference_that_matters() -> None:
    before = task()
    done = before.model_copy(update={"status": TaskStatus.done})
    assert describe("updated", before, done, "Ann", name_of) == "Ann completed a task: Migrate DB"
    assert describe("updated", done, before, "Ann", name_of) == "Ann reopened a task: Migrate DB"
    renamed = before.model_copy(update={"title": "Migrate the DB"})
    assert (
        describe("updated", before, renamed, "Ann", name_of)
        == 'Ann renamed a task "Migrate DB" to "Migrate the DB"'
    )
    assigned = before.model_copy(update={"assignee_id": BOB})
    assert describe("updated", before, assigned, "Ann", name_of) == (
        "Ann assigned a task to Bob: Migrate DB"
    )
    assert (
        describe("updated", assigned, before, "Ann", name_of) == "Ann unassigned a task: Migrate DB"
    )
    noted = before.model_copy(update={"notes": "psql"})
    assert describe("updated", before, noted, "Ann", name_of) == (
        "Ann edited the notes of a task: Migrate DB"
    )
    moved = before.model_copy(update={"rank": "-1"})
    assert describe("updated", before, moved, "Ann", name_of) == "Ann moved a task: Migrate DB"
    assert describe("updated", before, before, "Ann", name_of) == "Ann updated a task: Migrate DB"
    assert describe("updated", None, before, "Ann", name_of) == "Ann updated a task: Migrate DB"


def test_mine_is_assigned_to_me_or_unassigned_and_created_by_me() -> None:
    assert is_mine(task(), ME) is True
    assert is_mine(task(created_by=BOB), ME) is False
    assert is_mine(task(created_by=BOB, assignee_id=ME), ME) is True
    assert is_mine(task(assignee_id=BOB), ME) is False
    assert is_mine(None, ME) is False


def test_a_short_id_resolves_when_unique() -> None:
    a = task(id="0199a4c0-0000-7000-8000-000000000001")
    b = task(id="0199a4c0-0000-7000-8000-0000000000ab")
    assert resolve("0199a4c0-0000-7000-8000-000000000001", [a, b]) is a
    assert resolve("000000ab", [a, b]) is b
    assert resolve("00AB", [a, b]) is b
    with pytest.raises(LookupError, match="more than one"):
        resolve("", [a, b])
    with pytest.raises(LookupError, match="no task matches"):
        resolve("ffff", [a, b])
    assert short_id(a.id) == "00000001"


def test_the_table_has_a_header_and_one_line_per_task() -> None:
    """Every column of the header starts where its values do: "STATUS" is six
    characters, so a five-wide column pushed ASSIGNEE and TITLE one over."""
    a = task(id="0199a4c0-0000-7000-8000-000000000001", assignee_id=BOB)
    lines = task_table([a], name_of).splitlines()
    assert lines[0] == "ID        STATUS  ASSIGNEE      TITLE"
    assert lines[1] == "00000001  open    Bob           Migrate DB"
    for column in ("STATUS", "ASSIGNEE", "TITLE"):
        assert lines[0].index(column) == len(lines[0].split(column)[0])
    assert lines[0].index("ASSIGNEE") == lines[1].index("Bob")


def membership(slug: str, name: str, role: str = "member", kind: str = "team"):
    from acme.client.types import MembershipChoiceView

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
    from acme.apps.cli.model import choose_org

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
    from acme.apps.cli.model import choose_org

    ajax, mine = membership("ajax", "Ajax"), membership("ann-1x2y", "Ann", "owner", "personal")
    assert choose_org([ajax, mine], None) == mine
    assert choose_org([ajax, mine], "ajax") == ajax


def test_org_lines_sort_by_name_and_mark_the_current_org() -> None:
    from acme.apps.cli.model import org_lines

    lines = org_lines([membership("z-team", "Zeta"), membership("ajax", "Ajax", "owner")], "z-team")
    assert lines == "  ajax    Ajax (owner)\n* z-team  Zeta (member)"
    mine = org_lines([membership("ann-1x2y", "Ann", "owner", "personal")], None)
    assert mine == "  ann-1x2y  Ann (owner, personal)"


def a_file(name: str, size: int) -> FileView:
    return FileView(
        id=uuid4(),
        name=name,
        extension=name.rpartition(".")[2],
        content_type="application/pdf",
        size_bytes=size,
        purpose=FilePurpose.task_attachment,
        subject_id=uuid4(),
        status=FileStatus.stored,
        created_at=datetime.now(UTC),
        created_by=uuid4(),
        deleted_at=None,
    )


def test_a_size_reads_in_the_unit_a_person_reads() -> None:
    assert [human_size(n) for n in (0, 1023, 1024, 1536, 25 * 1024 * 1024)] == [
        "0 B",
        "1023 B",
        "1.0 KB",
        "1.5 KB",
        "25.0 MB",
    ]


def test_the_attachment_table_and_its_short_ids() -> None:
    files = [a_file("a.pdf", 10), a_file("b.pdf", 2048)]
    lines = attachment_table(files).splitlines()
    assert lines[0].split() == ["ID", "SIZE", "TYPE", "NAME"]
    assert lines[2].split() == [short_id(files[1].id), "2.0", "KB", "application/pdf", "b.pdf"]
    assert resolve_file(short_id(files[0].id), files) is files[0]
    with pytest.raises(LookupError, match="no attachment"):
        resolve_file("zzzzzzzz", files)


@pytest.mark.parametrize("text", ["soon", "2026-10-01T09:00", "+1d", "2026-02-30", "20261001"])
def test_a_due_date_is_a_date_and_never_a_time(text: str) -> None:
    from datetime import date

    from acme.apps.cli.model import parse_due

    assert parse_due(" 2026-10-01 ") == date(2026, 10, 1)
    with pytest.raises(ValueError, match="give one as 2026-10-01"):
        parse_due(text)
