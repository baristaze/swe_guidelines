"""checkers/src/arch_check/rules/om.py: the object-model rules, OM-01 to OM-17."""

import pytest

pytest.importorskip("tomllib")

from arch_check_fixtures import check, check_json, rules_found, write_project

OM = "om/src/acme/om"
BASE = f"{OM}/base.py"
TASK = f"{OM}/tasks/types/task.py"
TASK_IMPL = f"{OM}/tasks/impl/manager.py"
RULES = f"{OM}/tasks/rules.py"
ROW = f"{OM}/outbox/types/row.py"
SERVICE = "services/api/src/acme/services/api/routes.py"

BASE_SOURCE = '''\
from datetime import UTC, datetime
from uuid import UUID, uuid7

from pydantic import BaseModel, ConfigDict


def new_id() -> UUID:
    return uuid7()


def utcnow() -> datetime:
    return datetime.now(UTC)


class Platform(BaseModel):
    """Root of the object model. Holds no fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class Identifiable(Platform):
    id: UUID


class Named(Platform):
    name: str


class Created(Platform):
    created_at: datetime


class Trackable(Created):
    updated_at: datetime
    created_by: UUID
    updated_by: UUID


class SoftDeletable(Platform):
    deleted_at: datetime | None = None
    deleted_by: UUID | None = None


PROVENANCE_FIELDS = frozenset({"created_at", "created_by", "deleted_at", "deleted_by"})

EMPTY_UUID = UUID(int=0)
'''

TASK_SOURCE = """\
from uuid import UUID

from pydantic import Field

from acme.om.base import FrozenMapping, Identifiable, Named, Platform, SoftDeletable, Trackable


class Priority(Platform):
    level: int


class Task(Identifiable, Named, Trackable, SoftDeletable):
    title: str
    tags: tuple[str, ...] = ()
    labels: FrozenMapping = Field(default_factory=dict, validate_default=True)
    parent_id: UUID | None = None
    priority: Priority | None = None
"""

IMPL_SOURCE = """\
from acme.om.base import Platform, new_id
from acme.om.tasks.types import Task


class TaskOptions(Platform):
    page_size: int = 50


class TaskManagerImpl:
    def update(self, current: Task, changes: dict) -> Task:
        return Task.model_validate({**current.model_dump(), **changes})

    def touch(self, current: Task, when) -> Task:
        return current.model_copy(update={"updated_at": when, "id": new_id()})
"""

ROW_SOURCE = """\
from uuid import UUID

from acme.om.base import Created, Identifiable


class OutboxRow(Identifiable, Created):
    org_id: UUID
    actor_id: UUID
    request_id: UUID
    traceparent: str | None = None
    app: str
"""

GOOD: dict[str, str | None] = {
    "om/pyproject.toml": '[project]\nname = "acme-om"\ndependencies = ["pydantic>=2"]\n',
    "services/api/pyproject.toml": '[project]\nname = "acme-api"\ndependencies = ["acme-om", "fastapi"]\n',
    "workers/maintenance/pyproject.toml": '[project]\nname = "acme-maintenance"\ndependencies = ["acme_om"]\n',
    BASE: BASE_SOURCE,
    f"{OM}/tasks/__init__.py": "from .manager import TaskManagerInterface\n",
    f"{OM}/tasks/manager.py": "from abc import ABC\n\n\nclass TaskManagerInterface(ABC):\n    pass\n",
    f"{OM}/tasks/types/__init__.py": "from .task import Task\n",
    TASK: TASK_SOURCE,
    f"{OM}/tasks/impl/__init__.py": "",
    TASK_IMPL: IMPL_SOURCE,
    f"{OM}/tasks/storage/__init__.py": "",
    RULES: "from datetime import datetime, timedelta\n\n\ndef due(start: datetime, days: int) -> datetime:\n"
    "    return start + timedelta(days=days)\n",
    f"{OM}/tenancy/__init__.py": "from .manager import TenancyManagerInterface\n",
    f"{OM}/tenancy/manager.py": "class TenancyManagerInterface:\n    pass\n",
    f"{OM}/tenancy/types/__init__.py": "",
    f"{OM}/tenancy/impl/__init__.py": "",
    f"{OM}/tenancy/storage/__init__.py": "",
    f"{OM}/outbox/__init__.py": "from .relay import OutboxRelayInterface\n",
    f"{OM}/outbox/relay.py": "class OutboxRelayInterface:\n    pass\n",
    f"{OM}/outbox/impl/__init__.py": "",
    f"{OM}/outbox/storage/__init__.py": "",
    f"{OM}/outbox/types/__init__.py": "",
    ROW: ROW_SOURCE,
    f"{OM}/storage/__init__.py": "",
    f"{OM}/storage/tables/__init__.py": "class Tasks:\n    org_id: int\n    title: list\n",
    SERVICE: "from acme.om.tasks.types import Task\n\n\ndef show(task: Task) -> str:\n    return task.title\n",
}


def project(tmp_path, files=None, pyproject=None):
    """The good tree with `files` over it; a None value deletes that file."""
    merged = {**GOOD, **(files or {})}
    kwargs = {} if pyproject is None else {"pyproject": pyproject}
    write_project(tmp_path, {k: v for k, v in merged.items() if v is not None}, **kwargs)
    for rel, text in merged.items():
        if text is None and (tmp_path / rel).exists():
            (tmp_path / rel).unlink()
    return tmp_path


def run(tmp_path, rule, files=None, pyproject=None):
    project(tmp_path, files, pyproject)
    code, report = check_json(tmp_path, "--rule", rule)
    return code, report


def found(report):
    return [(r, p) for r, p, _ in rules_found(report)]


def messages(report):
    return [f["message"] for f in report["findings"]]


def test_the_good_tree_passes_every_om_rule(tmp_path):
    project(tmp_path)
    code, out, err = check(tmp_path, "--group", "om")
    assert code == 0, out + err
    assert "arch-check ok" in out


# --- OM-01


def test_a_distribution_importing_the_om_without_depending_on_it_is_om_01(tmp_path):
    code, report = run(tmp_path, "OM-01", {"services/api/pyproject.toml": '[project]\nname = "acme-api"\ndependencies = []\n'})
    assert code == 1
    assert found(report) == [("OM-01", "services/api/pyproject.toml")]
    assert "does not depend on acme-om" in messages(report)[0]


def test_an_unparseable_om_pyproject_is_reported_as_one_for_om_01(tmp_path):
    code, report = run(tmp_path, "OM-01", {"om/pyproject.toml": "[project\nname = 1\n"})
    assert code == 1
    assert found(report) == [("OM-01", "om/pyproject.toml")]
    assert report["findings"][0]["message"].startswith("om/pyproject.toml does not parse (")


def test_an_om_with_no_distribution_of_its_own_is_om_01(tmp_path):
    code, report = run(tmp_path, "OM-01", {"om/pyproject.toml": None})
    assert code == 1
    assert found(report) == [("OM-01", "om/pyproject.toml")]


# --- OM-02


def test_org_id_on_a_tenant_entity_is_om_02(tmp_path):
    source = TASK_SOURCE.replace("    title: str\n", "    title: str\n    org_id: UUID\n")
    code, report = run(tmp_path, "OM-02", {TASK: source})
    assert code == 1
    assert found(report) == [("OM-02", TASK)]
    assert "Task.org_id" in messages(report)[0]


def test_a_tenantless_entity_without_org_id_is_om_02(tmp_path):
    row = ROW_SOURCE.replace("    org_id: UUID\n", "")
    code, report = run(tmp_path, "OM-02", {ROW: row})
    assert code == 1
    assert "OutboxRow is read with no tenant" in messages(report)[0]


def test_the_tenantless_list_is_an_option(tmp_path):
    source = TASK_SOURCE.replace("    title: str\n", "    title: str\n    org_id: UUID\n")
    pyproject = '[tool.arch-check]\npackage = "acme"\n\n[tool.arch-check.options.OM-02]\ntenantless = ["OutboxRow", "Task"]\n'
    code, _ = run(tmp_path, "OM-02", {TASK: source}, pyproject)
    assert code == 0


def test_org_id_on_a_tenancy_type_is_not_om_02(tmp_path):
    membership = (
        "from uuid import UUID\n\nfrom acme.om.base import Identifiable\n\n\n"
        "class Membership(Identifiable):\n    org_id: UUID\n    user_id: UUID\n"
    )
    code, _ = run(tmp_path, "OM-02", {f"{OM}/tenancy/types/membership.py": membership})
    assert code == 0
    pyproject = '[tool.arch-check]\npackage = "acme"\n\n[tool.arch-check.options.OM-16]\nnamespace = "identity"\n'
    code, report = run(tmp_path, "OM-02", {f"{OM}/tenancy/types/membership.py": membership}, pyproject)
    assert code == 1
    assert "Membership.org_id" in messages(report)[0]


# --- OM-03


def test_a_field_added_to_a_mixin_is_om_03(tmp_path):
    source = BASE_SOURCE.replace(
        "class Named(Platform):\n    name: str\n", "class Named(Platform):\n    name: str\n    slug: str\n"
    )
    code, report = run(tmp_path, "OM-03", {BASE: source})
    assert code == 1
    assert "Named declares slug beyond its fields" in messages(report)[0]


def test_a_field_on_the_root_is_om_03(tmp_path):
    source = BASE_SOURCE.replace('extra="forbid")\n', 'extra="forbid")\n\n    version: int = 1\n')
    code, report = run(tmp_path, "OM-03", {BASE: source})
    assert code == 1
    assert "the root Platform declares version" in messages(report)[0]


def test_an_entity_redeclaring_a_mixin_field_is_om_03(tmp_path):
    source = TASK_SOURCE.replace("    title: str\n", "    title: str\n    created_at: str\n")
    code, report = run(tmp_path, "OM-03", {TASK: source})
    assert code == 1
    assert found(report) == [("OM-03", TASK)]
    assert "Task redeclares created_at, which Created already declares" in messages(report)[0]


def test_provenance_fields_wrong_or_repeated_is_om_03(tmp_path):
    base = BASE_SOURCE.replace('"deleted_by"})', '"deleted_by", "updated_by"})')
    code, report = run(tmp_path, "OM-03", {BASE: base, TASK_IMPL: IMPL_SOURCE + "\nPROVENANCE_FIELDS = frozenset()\n"})
    assert code == 1
    assert sorted(found(report)) == [("OM-03", BASE), ("OM-03", TASK_IMPL)]


def test_a_local_clock_is_om_03(tmp_path):
    source = "from datetime import datetime\n\n\ndef stamp():\n    return datetime.now()\n"
    code, report = run(tmp_path, "OM-03", {SERVICE: source})
    assert code == 1
    assert found(report) == [("OM-03", SERVICE)]


def test_an_outbox_row_without_provenance_or_with_created_by_is_om_03(tmp_path):
    row = ROW_SOURCE.replace("    app: str\n", "    created_by: UUID\n")
    code, report = run(tmp_path, "OM-03", {ROW: row})
    assert code == 1
    assert any("OutboxRow lacks app" in m for m in messages(report))
    assert any("OutboxRow has a created_by" in m for m in messages(report))


# --- OM-04


def test_mixins_out_of_order_are_om_04(tmp_path):
    source = TASK_SOURCE.replace("Task(Identifiable, Named, Trackable, SoftDeletable)", "Task(Trackable, Identifiable)")
    code, report = run(tmp_path, "OM-04", {TASK: source})
    assert code == 1
    assert found(report) == [("OM-04", TASK)]
    assert "Task(Trackable, Identifiable)" in messages(report)[0]


def test_mixins_reached_through_a_re_export_are_ordered_too(tmp_path):
    files = {
        f"{OM}/common.py": "from acme.om.base import Identifiable as Ident, Named\n",
        TASK: "from acme.om import common\n\n\nclass Task(common.Named, common.Ident):\n    title: str\n",
    }
    code, report = run(tmp_path, "OM-04", files)
    assert code == 1
    assert found(report) == [("OM-04", TASK)]


def test_a_new_trait_is_not_ordered_by_om_04(tmp_path):
    base = BASE_SOURCE + "\n\nclass Pinnable(Platform):\n    pinned: bool = False\n"
    task = TASK_SOURCE.replace(
        "Task(Identifiable, Named, Trackable, SoftDeletable)", "Task(Pinnable, Identifiable, Named, Trackable)"
    ).replace("import FrozenMapping,", "import FrozenMapping, Pinnable,")
    code, _ = run(tmp_path, "OM-04", {BASE: base, TASK: task})
    assert code == 0
    code, report = run(tmp_path, "OM-04", {BASE: base, TASK: task.replace("Named, Trackable)", "Trackable, Named)")})
    assert code == 1
    assert found(report) == [("OM-04", TASK)]


# --- OM-05


def test_a_mixin_with_a_method_is_om_05(tmp_path):
    source = BASE_SOURCE.replace(
        "class Named(Platform):\n    name: str\n",
        "class Named(Platform):\n    name: str\n\n    def label(self) -> str:\n        return self.name\n",
    )
    code, report = run(tmp_path, "OM-05", {BASE: source})
    assert code == 1
    assert "Named.label: a mixin is a promise" in messages(report)[0]


def test_a_validator_on_a_mixin_is_not_om_05(tmp_path):
    source = BASE_SOURCE.replace(
        "class Named(Platform):\n    name: str\n",
        "class Named(Platform):\n    name: str\n\n    @field_validator('name')\n    def strip(cls, v):\n        return v\n",
    )
    code, _ = run(tmp_path, "OM-05", {BASE: source})
    assert code == 0


def test_an_entity_inheriting_from_an_entity_is_om_05(tmp_path):
    code, report = run(tmp_path, "OM-05", {TASK: TASK_SOURCE + "\n\nclass SubTask(Task):\n    step: int\n"})
    assert code == 1
    assert "SubTask inherits from Task" in messages(report)[0]


def test_a_value_object_extending_a_value_object_is_not_om_05(tmp_path):
    source = TASK_SOURCE + "\n\nclass UrgentPriority(Priority):\n    reason: str\n"
    code, _ = run(tmp_path, "OM-05", {TASK: source})
    assert code == 0


# --- OM-07


def test_a_root_that_does_not_forbid_extras_is_om_07(tmp_path):
    code, report = run(tmp_path, "OM-07", {BASE: BASE_SOURCE.replace('extra="forbid"', 'extra="ignore"')})
    assert code == 1
    assert found(report) == [("OM-07", BASE), ("OM-07", BASE)]


def test_a_chain_class_relaxing_extras_is_om_07(tmp_path):
    source = TASK_SOURCE.replace(
        "class Priority(Platform):\n", 'class Priority(Platform):\n    model_config = ConfigDict(extra="allow")\n'
    )
    code, report = run(tmp_path, "OM-07", {TASK: source})
    assert code == 1
    assert found(report) == [("OM-07", TASK)]
    assert "Priority sets extra='allow'" in messages(report)[0]


def test_a_class_keyword_relaxing_extras_is_om_07(tmp_path):
    source = TASK_SOURCE.replace("class Priority(Platform):", 'class Priority(Platform, extra="ignore"):')
    code, report = run(tmp_path, "OM-07", {TASK: source})
    assert code == 1
    assert found(report) == [("OM-07", TASK)]


def test_a_merged_config_or_a_nested_config_relaxing_extras_is_om_07(tmp_path):
    merged = TASK_SOURCE.replace(
        "class Priority(Platform):\n",
        'class Priority(Platform):\n    model_config = Platform.model_config | {"extra": "allow"}\n',
    )
    code, report = run(tmp_path, "OM-07", {TASK: merged})
    assert (code, found(report)) == (1, [("OM-07", TASK)])
    nested_config = 'class Priority(Platform):\n    class Config:\n        extra = "allow"\n\n'
    nested = TASK_SOURCE.replace("class Priority(Platform):\n", nested_config)
    code, report = run(tmp_path, "OM-07", {TASK: nested})
    assert (code, found(report)) == (1, [("OM-07", TASK)])


def test_a_class_on_the_chain_through_a_star_import_is_still_read_by_om_07(tmp_path):
    source = TASK_SOURCE.replace(
        "class Priority(Platform):\n", 'class Priority(Platform):\n    model_config = ConfigDict(extra="allow")\n'
    )
    lines = source.splitlines(keepends=True)
    starred = "".join("from acme.om.base import *\n" if "import" in line and "acme.om.base" in line else line for line in lines)
    code, report = run(tmp_path, "OM-07", {TASK: starred})
    assert (code, found(report)) == (1, [("OM-07", TASK)])


def test_a_nested_config_unfreezing_is_om_11(tmp_path):
    unfrozen = "class Priority(Platform):\n    class Config:\n        frozen = False\n\n"
    source = TASK_SOURCE.replace("class Priority(Platform):\n", unfrozen)
    code, report = run(tmp_path, "OM-11", {TASK: source})
    assert (code, found(report)) == (1, [("OM-11", TASK)])


def test_a_model_off_the_chain_may_set_extras(tmp_path):
    source = (
        'from pydantic import BaseModel, ConfigDict\n\n\nclass Body(BaseModel):\n    model_config = ConfigDict(extra="ignore")\n'
    )
    code, _ = run(tmp_path, "OM-07", {SERVICE: source})
    assert code == 0


# --- OM-09


def test_an_interface_taking_kwargs_or_a_dict_filter_is_om_09(tmp_path):
    source = (
        "from typing import Any\n\n\nclass TaskStorageInterface:\n"
        "    async def read_tasks(self, org_id, where: dict[str, Any], **extra): ...\n"
        "    async def count(self, org_id, filter: TaskFilter): ...\n"
    )
    code, report = run(tmp_path, "OM-09", {f"{OM}/tasks/storage/__init__.py": source})
    assert code == 1
    assert len(report["findings"]) == 2
    assert any("takes **extra" in m for m in messages(report))
    assert any("where is dict[str, Any]" in m for m in messages(report))


def test_an_ordering_as_a_string_is_not_om_09(tmp_path):
    source = "class TaskStorageInterface:\n    async def read_tasks(self, org_id, order_by: str): ...\n"
    code, _ = run(tmp_path, "OM-09", {f"{OM}/tasks/storage/__init__.py": source})
    assert code == 0
    code, _ = run(tmp_path, "OM-09", {f"{OM}/tasks/storage/__init__.py": source.replace("order_by", "group_by")})
    assert code == 1


# --- OM-10


def test_an_om_with_no_root_is_om_07(tmp_path):
    code, report = run(tmp_path, "OM-07", {BASE: "class Platform:\n    pass\n"})
    assert code == 1
    assert messages(report) == ["no class in acme.om.base extends BaseModel; the OM root does"]
    code, report = run(tmp_path, "OM-07", {BASE: None})
    assert code == 1
    assert messages(report) == ["acme.om has no base module; the root and the mixins live in acme.om.base"]


def test_model_copy_fed_a_dump_is_om_10(tmp_path):
    source = IMPL_SOURCE + (
        "\n\ndef bad(current: Task, other: Task) -> Task:\n"
        "    changes = other.model_dump()\n"
        "    return current.model_copy(update=changes)\n"
    )
    code, report = run(tmp_path, "OM-10", {TASK_IMPL: source})
    assert code == 1
    assert "model_copy(update=...) fed a dump" in messages(report)[0]


def test_model_copy_fed_an_annotated_dump_is_om_10(tmp_path):
    source = IMPL_SOURCE + (
        "\n\ndef bad(current: Task, other: Task) -> Task:\n"
        "    update: dict = {**other.model_dump()}\n"
        "    return current.model_copy(update=update)\n"
    )
    code, report = run(tmp_path, "OM-10", {TASK_IMPL: source})
    assert code == 1
    assert "model_copy(update=...) fed a dump" in messages(report)[0]


@pytest.mark.parametrize(
    "update",
    ["{'owner': other.model_dump()}", "dict(owner=other.model_dump())", "{'title': 't', **{'owner': other.model_dump()}}"],
)
def test_model_copy_fed_a_dump_as_one_field_is_om_10(tmp_path, update):
    source = IMPL_SOURCE + (f"\n\ndef bad(current: Task, other: Task) -> Task:\n    return current.model_copy(update={update})\n")
    code, report = run(tmp_path, "OM-10", {TASK_IMPL: source})
    assert code == 1
    assert "model_copy(update=...) fed a dump" in messages(report)[0]


def test_a_scalar_taken_out_of_a_dump_is_not_om_10(tmp_path):
    source = IMPL_SOURCE + (
        "\n\ndef fine(current: Task, other: Task) -> Task:\n"
        "    title = other.model_dump()['title']\n"
        "    return current.model_copy(update={'title': title, 'n': other.model_dump()['n']})\n"
    )
    code, _ = run(tmp_path, "OM-10", {TASK_IMPL: source})
    assert code == 0


def test_model_validate_on_an_entity_is_om_10(tmp_path):
    source = "from acme.om.tasks.types import Task\n\n\ndef again(task: Task) -> Task:\n    return Task.model_validate(task)\n"
    code, report = run(tmp_path, "OM-10", {SERVICE: source})
    assert code == 1
    assert found(report) == [("OM-10", SERVICE)]


def test_a_view_built_from_an_entity_is_not_om_10(tmp_path):
    source = (
        "from pydantic import BaseModel\n\n"
        "from acme.om.base import Identifiable\n"
        "from acme.om.tasks.types import Task\n\n\n"
        "class TaskView(BaseModel):\n    title: str\n\n\n"
        "def view(task: Task) -> TaskView:\n"
        "    return TaskView.model_validate(task, from_attributes=True)\n"
    )
    code, _ = run(tmp_path, "OM-10", {SERVICE: source})
    assert code == 0
    ancestor = source.replace("TaskView.model_validate(task", "Identifiable.model_validate(task")
    code, report = run(tmp_path, "OM-10", {SERVICE: ancestor})
    assert code == 1
    assert found(report) == [("OM-10", SERVICE)]


def test_assigning_to_an_entity_attribute_is_om_10(tmp_path):
    source = (
        "from acme.om.tasks.types import Task\n\n\n"
        "def close(task: Task | None, body) -> None:\n"
        "    task.title = 'done'\n"
        "    body.title = 'fine'\n"
        "    object.__setattr__(task, 'title', 'x')\n"
    )
    code, report = run(tmp_path, "OM-10", {SERVICE: source})
    assert code == 1
    assert len(report["findings"]) == 2


def test_frozen_false_is_om_10_and_om_11(tmp_path):
    source = TASK_SOURCE.replace(
        "class Priority(Platform):\n", "class Priority(Platform):\n    model_config = ConfigDict(frozen=False)\n"
    )
    project(tmp_path, {TASK: source})
    code, report = check_json(tmp_path, "--rule", "OM-10,OM-11")
    assert code == 1
    assert sorted(found(report)) == [("OM-10", TASK), ("OM-11", TASK)]


# --- OM-11


def test_a_root_that_is_not_frozen_is_om_11(tmp_path):
    code, report = run(tmp_path, "OM-11", {BASE: BASE_SOURCE.replace("frozen=True, ", "")})
    assert code == 1
    assert "does not set frozen=True" in messages(report)[0]


# --- OM-12


@pytest.mark.parametrize(
    "rel,source",
    [
        (SERVICE, "from uuid import uuid4\n"),
        (SERVICE, "import uuid\n\nx = uuid.uuid4()\n"),
        (
            SERVICE,
            "import uuid\nfrom pydantic import BaseModel, Field\n\n"
            "class B(BaseModel):\n    id: str = Field(default_factory=uuid.uuid4)\n",
        ),
        (TASK_IMPL, "from uuid import uuid7\n"),
        ("workers/maintenance/src/acme/workers/maintenance/__init__.py", "import ulid\n"),
    ],
)
def test_an_id_factory_other_than_new_id_is_om_12(tmp_path, rel, source):
    code, report = run(tmp_path, "OM-12", {rel: source})
    assert code == 1
    assert {p for _, p in found(report)} == {rel}


def test_a_client_minting_an_idempotency_key_is_not_om_12(tmp_path):
    code, _ = run(tmp_path, "OM-12", {"clients/python/src/acme/client/__init__.py": "from uuid import uuid4\n"})
    assert code == 0


# --- OM-13


def test_a_second_zero_uuid_is_om_13(tmp_path):
    source = 'from uuid import UUID\n\nNO_WAREHOUSE = UUID("00000000-0000-0000-0000-000000000000")\n'
    code, report = run(tmp_path, "OM-13", {SERVICE: source})
    assert code == 1
    assert found(report) == [("OM-13", SERVICE)]


def test_an_optional_reference_defaulting_to_empty_uuid_is_om_13(tmp_path):
    source = TASK_SOURCE.replace("parent_id: UUID | None = None", "parent_id: UUID | None = EMPTY_UUID")
    code, report = run(tmp_path, "OM-13", {TASK: source})
    assert code == 1
    assert "optional reference defaults to EMPTY_UUID" in messages(report)[0]


def test_a_required_reference_signed_empty_uuid_is_not_om_13(tmp_path):
    source = TASK_SOURCE.replace("parent_id: UUID | None = None", "owner_id: UUID = EMPTY_UUID")
    code, _ = run(tmp_path, "OM-13", {TASK: source})
    assert code == 0


def test_infra_keeps_its_own_system_scope(tmp_path):
    source = "from uuid import UUID\n\nSYSTEM_SCOPE = UUID(int=0)\n\n\nclass InfraRoot:\n    pass\n"
    code, _ = run(tmp_path, "OM-13", {"infra/src/acme/infra/__init__.py": source})
    assert code == 0


# --- OM-14


def test_a_namespace_missing_its_shape_is_om_14(tmp_path):
    code, report = run(tmp_path, "OM-14", {f"{OM}/tenancy/types/__init__.py": None})
    assert code == 1
    assert found(report) == [("OM-14", f"{OM}/tenancy/__init__.py")]
    assert "namespace tenancy has no types/" in messages(report)[0]


def test_a_namespace_not_re_exporting_its_interface_is_om_14(tmp_path):
    code, report = run(tmp_path, "OM-14", {f"{OM}/tasks/__init__.py": ""})
    assert code == 1
    assert "does not re-export its interface" in messages(report)[0]


def test_an_entity_next_to_the_impl_is_om_14(tmp_path):
    source = IMPL_SOURCE.replace("from acme.om.base import", "from acme.om.base import Identifiable,")
    source += "\n\nclass Draft(Identifiable):\n    body: str\n"
    code, report = run(tmp_path, "OM-14", {TASK_IMPL: source})
    assert code == 1
    assert found(report) == [("OM-14", TASK_IMPL)]


def test_the_entry_module_of_a_namespace_is_an_option(tmp_path):
    files = {
        f"{OM}/tasks/__init__.py": "from .board import TaskBoardInterface\n",
        f"{OM}/tasks/board.py": "",
        f"{OM}/tasks/manager.py": None,
    }
    code, _ = run(tmp_path, "OM-14", files)
    assert code == 1
    pyproject = '[tool.arch-check]\npackage = "acme"\n\n[tool.arch-check.options.OM-14]\nentry = { tasks = "board" }\n'
    code, _ = run(tmp_path, "OM-14", files, pyproject)
    assert code == 0


@pytest.mark.parametrize("value", ["{ tasks = 5 }", "{ tasks = [5] }", "{ tasks = [] }", '{ tasks = "" }'])
def test_a_malformed_entry_option_exits_2(tmp_path, value):
    pyproject = f'[tool.arch-check]\npackage = "acme"\n\n[tool.arch-check.options.OM-14]\nentry = {value}\n'
    write_project(tmp_path, pyproject=pyproject)
    code, _, err = check(tmp_path, "--rule", "OM-14")
    assert code == 2
    assert "[tool.arch-check.options.OM-14] `entry`" in err


def test_the_outbox_entry_module_is_manager_or_relay(tmp_path):
    files = {
        f"{OM}/outbox/__init__.py": "from .manager import OutboxManagerInterface\n",
        f"{OM}/outbox/manager.py": "class OutboxManagerInterface:\n    pass\n",
        f"{OM}/outbox/relay.py": None,
    }
    code, _ = run(tmp_path, "OM-14", files)
    assert code == 0
    code, report = run(tmp_path, "OM-14", {**files, f"{OM}/outbox/manager.py": None})
    assert code == 1
    assert "namespace outbox has no manager.py" in messages(report)[0]


# --- OM-15


@pytest.mark.parametrize(
    "source",
    [
        "from acme.om.tasks.storage import TaskStorageInterface\n",
        "from acme.infra.settings import Settings\n",
        "import os\n",
        "from acme.om.base import utcnow\n",
        "from datetime import datetime\n\n\ndef late(due):\n    return due < datetime.now()\n",
        "async def fetch():\n    return 1\n",
    ],
)
def test_an_impure_rules_module_is_om_15(tmp_path, source):
    code, report = run(tmp_path, "OM-15", {RULES: source})
    assert code == 1
    assert {p for _, p in found(report)} == {RULES}


def test_a_settings_named_type_or_an_infra_import_is_not_om_15(tmp_path):
    source = (
        "from acme.infra import InfraRoot\n"
        "from acme.om.tasks.types.notification_settings import NotificationSettings\n\n\n"
        "def quiet(s: NotificationSettings) -> bool:\n    return s.muted\n"
    )
    code, _ = run(tmp_path, "OM-15", {RULES: source})
    assert code == 0
    code, _ = run(tmp_path, "OM-15", {RULES: "from acme.om.tasks.settings import Settings\n"})
    assert code == 1


# --- OM-16


def test_identity_in_the_base_module_is_om_16(tmp_path):
    code, report = run(tmp_path, "OM-16", {BASE: BASE_SOURCE + "\n\nclass User(Identifiable):\n    email: str\n"})
    assert code == 1
    assert found(report) == [("OM-16", BASE)]


def test_no_tenancy_namespace_is_om_16(tmp_path):
    missing = {rel: None for rel in GOOD if rel.startswith(f"{OM}/tenancy/")}
    code, report = run(tmp_path, "OM-16", missing)
    assert code == 1
    assert "has no tenancy namespace" in messages(report)[0]


# --- OM-17


@pytest.mark.parametrize(
    "annotation",
    [
        "list[str]",
        "dict[str, int]",
        "set[int]",
        "Mapping[str, int]",
        "tuple[list[int], ...]",
        "'List[int]'",
        "Sequence[str]",
        "Collection[str]",
        "Iterable[str]",
        "AbstractSet[str]",
        "collections.abc.MutableSequence[str]",
    ],
)
def test_a_mutable_field_is_om_17(tmp_path, annotation):
    source = TASK_SOURCE.replace("    tags: tuple[str, ...] = ()\n", f"    tags: {annotation}\n")
    code, report = run(tmp_path, "OM-17", {TASK: source})
    assert code == 1
    assert found(report) == [("OM-17", TASK)]


def test_a_literal_naming_a_container_is_not_om_17(tmp_path):
    source = TASK_SOURCE.replace("    tags: tuple[str, ...] = ()\n", '    kind: Literal["list", "dict"] = "list"\n')
    source = "from typing import Literal\n" + source
    code, _ = run(tmp_path, "OM-17", {TASK: source})
    assert code == 0
    code, _ = run(tmp_path, "OM-17", {TASK: source.replace('Literal["list", "dict"] = "list"', 'Literal["a"] | list[int]')})
    assert code == 1


def test_a_frozen_mapping_default_without_validation_is_om_17(tmp_path):
    source = TASK_SOURCE.replace("Field(default_factory=dict, validate_default=True)", "Field(default_factory=dict)")
    code, report = run(tmp_path, "OM-17", {TASK: source})
    assert code == 1
    assert "validate_default=True" in messages(report)[0]


def test_a_table_class_off_the_chain_may_hold_a_list(tmp_path):
    code, _ = run(tmp_path, "OM-17")
    assert code == 0


# --- names read through imports


def test_a_root_on_an_aliased_base_model_keeps_the_chain(tmp_path):
    source = BASE_SOURCE.replace(
        "from pydantic import BaseModel, ConfigDict", "from pydantic import BaseModel as Model, ConfigDict"
    )
    source = source.replace("class Platform(BaseModel):", "class Platform(Model):")
    project(tmp_path, {BASE: source})
    assert check(tmp_path, "--group", "om")[0] == 0
    bad = source.replace('model_config = ConfigDict(frozen=True, extra="forbid")', "model_config = ConfigDict(frozen=True)")
    code, report = run(tmp_path, "OM-07", {BASE: bad})
    assert code == 1
    assert found(report) == [("OM-07", BASE)]


def test_an_org_id_brought_in_by_a_mixin_is_om_02(tmp_path):
    scoped = "from uuid import UUID\n\nfrom acme.om.base import Platform\n\n\nclass OrgScoped(Platform):\n    org_id: UUID\n"
    source = TASK_SOURCE.replace(
        "from acme.om.base import", "from acme.om.tasks.scoped import OrgScoped\nfrom acme.om.base import"
    ).replace(
        "class Task(Identifiable, Named, Trackable, SoftDeletable):",
        "class Task(Identifiable, Named, Trackable, SoftDeletable, OrgScoped):",
    )
    code, report = run(tmp_path, "OM-02", {f"{OM}/tasks/scoped.py": scoped, TASK: source})
    assert code == 1
    assert found(report) == [("OM-02", TASK)]
    assert "Task inherits org_id from OrgScoped" in messages(report)[0]


def test_a_flat_layout_is_one_distribution_per_pyproject_for_om_01(tmp_path):
    files = {
        "om/pyproject.toml": '[project]\nname = "acme-om"\ndependencies = []\n',
        "om/acme/om/__init__.py": "",
        "services/api/pyproject.toml": '[project]\nname = "acme-api"\ndependencies = ["acme-om"]\n',
        "services/api/acme/services/api/main.py": "from acme.om import base\n",
    }
    pyproject = '[tool.arch-check]\npackage = "acme"\nsrc = ["om", "services/*"]\n'
    write_project(tmp_path, files, pyproject=pyproject)
    code, report = check_json(tmp_path, "--rule", "OM-01")
    assert (code, rules_found(report)) == (0, [])
    (tmp_path / "services/api/pyproject.toml").write_text('[project]\nname = "acme-api"\ndependencies = []\n')
    code, report = check_json(tmp_path, "--rule", "OM-01")
    assert (code, rules_found(report)) == (1, [("OM-01", "services/api/pyproject.toml", 1)])


@pytest.mark.parametrize(
    "source",
    [
        "import uuid as u\n\nx = u.uuid4()\n",
        "from uuid import uuid4 as mint\n",
    ],
)
def test_an_aliased_id_factory_is_om_12(tmp_path, source):
    code, report = run(tmp_path, "OM-12", {SERVICE: source})
    assert code == 1
    assert found(report) == [("OM-12", SERVICE)]


def test_an_annotated_empty_uuid_in_the_base_module_is_not_om_13(tmp_path):
    code, report = run(
        tmp_path, "OM-13", {BASE: BASE_SOURCE.replace("EMPTY_UUID = UUID(int=0)", "EMPTY_UUID: UUID = UUID(int=0)")}
    )
    assert (code, report["findings"]) == (0, [])


def test_an_impl_module_where_a_package_belongs_is_om_14(tmp_path):
    code, report = run(tmp_path, "OM-14", {f"{OM}/tasks/impl/__init__.py": None, TASK_IMPL: None, f"{OM}/tasks/impl.py": ""})
    assert code == 1
    assert found(report) == [("OM-14", f"{OM}/tasks/impl.py")]
    assert "has impl.py where impl/ belongs" in messages(report)[0]


@pytest.mark.parametrize(
    "source,bad",
    [
        ("def ends(slot):\n    return slot.end_time.time()\n", False),
        ("from datetime import datetime as dt\n\n\ndef late(due):\n    return due < dt.now()\n", True),
        ("import datetime as d\n\n\ndef today():\n    return d.date.today()\n", True),
    ],
)
def test_a_clock_read_through_an_alias_is_om_15(tmp_path, source, bad):
    code, _ = run(tmp_path, "OM-15", {RULES: source})
    assert code == (1 if bad else 0)


def test_a_clock_read_through_an_alias_is_om_03(tmp_path):
    source = "from datetime import datetime as dt\n\n\ndef stamp():\n    return dt.now()\n"
    code, report = run(tmp_path, "OM-03", {SERVICE: source})
    assert code == 1
    assert found(report) == [("OM-03", SERVICE)]
    code, _ = run(tmp_path, "OM-03", {SERVICE: "def stamp(clock):\n    return clock.now()\n"})
    assert code == 0


def test_annotated_metadata_is_not_om_17(tmp_path):
    source = "from typing import Annotated\n" + TASK_SOURCE.replace(
        "    tags: tuple[str, ...] = ()\n", "    tags: Annotated[tuple[str, ...], Coerce(list)] = ()\n"
    )
    code, _ = run(tmp_path, "OM-17", {TASK: source})
    assert code == 0
    code, _ = run(tmp_path, "OM-17", {TASK: source.replace("Annotated[tuple[str, ...]", "Annotated[list[str]")})
    assert code == 1
