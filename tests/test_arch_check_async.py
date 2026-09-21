"""checkers/src/arch_check/rules/async_.py: the async lenses a parser decides.

`GOOD` is an infra layer shaped the way the guideline prescribes: one
root with a getter per capability, a cache, topics, buckets, and
secrets each with an interface and a local impl, a container that
picks the impls, a socket edge that runs its own tasks, the work item
and the outbox row. Every rule passes on it; each fail case bends one
file.
"""

import pytest

pytest.importorskip("tomllib")

from arch_check_fixtures import check_json, rules_found, write_project

INFRA = "infra/src/acme/infra"
OM = "om/src/acme/om"
API = "services/api/src/acme/services/api"
ROOT = f"{INFRA}/root.py"
CACHE = f"{INFRA}/cache/__init__.py"
TOPICS = f"{INFRA}/topics/__init__.py"
BUCKETS = f"{INFRA}/buckets/__init__.py"
SECRETS_LOCAL = f"{INFRA}/secrets/local.py"
CONTAINER = f"{API}/container.py"
WORK = f"{OM}/work/types/work_item.py"
WORK_TABLE = f"{OM}/work/storage/tables/work_items.py"
OUTBOX = f"{OM}/outbox/types/row.py"
MANAGER = f"{OM}/orders/impl.py"

GOOD: dict[str, str] = {
    ROOT: """\
from abc import ABC, abstractmethod


class InfraInterface(ABC):
    @abstractmethod
    def get_cache(self, scope: CacheScope) -> CacheInterface: ...

    @abstractmethod
    def get_topics(self) -> TopicsInterface: ...

    @abstractmethod
    def get_buckets(self) -> BucketsInterface: ...

    @abstractmethod
    def get_secrets(self) -> SecretsInterface: ...

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...
""",
    f"{INFRA}/impl/__init__.py": "",
    f"{INFRA}/impl/local.py": """\
from acme.infra.cache.memory import CacheMemoryImpl


class InfraLocalImpl(InfraInterface):
    def get_cache(self, scope):
        return self._caches.get_cache(scope)
""",
    CACHE: """\
from abc import ABC, abstractmethod
from enum import Enum


class CacheScope(str, Enum):
    RATE_LIMIT = "rate_limit"


class CacheInterface(ABC):
    @abstractmethod
    async def get(self, org_id, key) -> bytes | None: ...

    @abstractmethod
    def describe(self) -> str: ...
""",
    f"{INFRA}/cache/memory.py": """\
class CacheMemoryImpl(CacheInterface):
    def describe(self) -> str:
        return "cache=memory"


class CacheQuietImpl(CacheMemoryImpl):
    pass
""",
    TOPICS: """\
from abc import ABC, abstractmethod
from collections.abc import Callable
from enum import Enum

from pydantic import BaseModel, ConfigDict


class TopicPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    idempotency_key: UUID
    produced_at: datetime


class Topics(str, Enum):
    WORK_AVAILABLE = "work_available"


class WorkAvailablePayload(TopicPayload):
    lane: str


TOPIC_PAYLOADS: dict[Topics, type[TopicPayload]] = {
    Topics.WORK_AVAILABLE: WorkAvailablePayload,
}


class TopicsInterface(ABC):
    @abstractmethod
    async def publish(self, topic: Topics, payload: TopicPayload) -> None: ...

    @abstractmethod
    def subscribe(self, topic: Topics, consumer: str, handler) -> Callable[[], None]: ...

    @abstractmethod
    def describe(self) -> str: ...
""",
    f"{INFRA}/topics/memory.py": """\
class TopicsMemoryImpl(TopicsInterface):
    def describe(self) -> str:
        return "topics=memory"
""",
    BUCKETS: """\
from abc import ABC, abstractmethod
from enum import Enum


class Buckets(str, Enum):
    EXPORTS = "exports"


class BucketsInterface(ABC):
    @abstractmethod
    async def get(self, org_id, bucket: Buckets, key: str) -> bytes: ...

    @abstractmethod
    async def list(self, org_id, bucket: Buckets, prefix: str, limit: int) -> list[str]: ...

    @abstractmethod
    def describe(self) -> str: ...
""",
    f"{INFRA}/buckets/local.py": """\
class BucketsLocalImpl(BucketsInterface):
    def describe(self) -> str:
        return "buckets=local"
""",
    f"{INFRA}/secrets/__init__.py": """\
from abc import ABC, abstractmethod


class SecretsInterface(ABC):
    @abstractmethod
    async def get(self, name: str) -> str: ...

    @abstractmethod
    def describe(self) -> str: ...
""",
    SECRETS_LOCAL: """\
import stat


class SecretsLocalImpl(SecretsInterface):
    def describe(self) -> str:
        return "secrets=local"

    async def start(self) -> None:
        await self._holder.open()

    def _read(self) -> str:
        mode = stat.S_IMODE(self._file.stat().st_mode)
        if mode & 0o077:
            raise PermissionError(self._file)
        return self._file.read_text()
""",
    CONTAINER: """\
from acme.infra.cache import CacheScope
from acme.infra.impl.local import InfraLocalImpl


def build(infra):
    return infra.get_cache(CacheScope.RATE_LIMIT)
""",
    f"{API}/realtime/socket.py": """\
import asyncio


async def serve(ws, loop):
    drainer = asyncio.create_task(ws.drain())
    loop.call_later(5, ws.close)
    return drainer
""",
    f"{API}/routers/orders.py": """\
async def create_order(ctx, orders, body):
    return await orders.create_order(ctx, body)
""",
    WORK: """\
from enum import Enum


class WorkKind(str, Enum):
    NOOP = "noop"


class WorkItem(Identifiable, Trackable):
    kind: WorkKind
    target_id: UUID
    idempotency_key: UUID
    request_id: UUID
    traceparent: str | None = None
    lane: str
    status: str
    available_at: datetime
    claimed_by: str | None
    claim_token: UUID | None
    lease_expires_at: datetime | None
    attempts: int


WORK_PAYLOADS = {WorkKind.NOOP: NoopPayload}
""",
    WORK_TABLE: """\
class WorkItems(IdentifiableMixin, TrackableMixin, Base):
    __tablename__ = "work_items"
    __table_args__ = (Index("uq_work_items_idempotency_key", "idempotency_key", unique=True),)
    idempotency_key: Mapped[UUID]
""",
    OUTBOX: """\
class OutboxRow(Identifiable, Created):
    request_id: UUID
    traceparent: str | None = None
""",
    "deployment/terraform/main.tf": """\
resource "aws_cloudwatch_event_rule" "deploys" {
  event_pattern = jsonencode({ source = ["aws.ecs"] })
}
""",
}

RULES = [
    "ASY-01",
    "ASY-02",
    "ASY-03",
    "ASY-04",
    "ASY-07",
    "ASY-08",
    "ASY-09",
    "ASY-15",
    "ASY-16",
    "ASY-19",
    "ASY-28",
    "ASY-29",
]


def run(tmp_path, rule, files=None, pyproject=None):
    kwargs = {"pyproject": pyproject} if pyproject else {}
    write_project(tmp_path, {**GOOD, **(files or {})}, **kwargs)
    return check_json(tmp_path, "--rule", rule)


def edit(rel, old, new):
    assert old in GOOD[rel], f"{rel} does not contain {old!r}"
    return {rel: GOOD[rel].replace(old, new)}


def messages(report):
    return sorted(f["message"] for f in report["findings"])


def test_the_group_lists_every_rule(tmp_path):
    write_project(tmp_path, GOOD)
    _, report = check_json(tmp_path, "--group", "async")
    assert sorted(r["id"] for r in report["rules_run"]) == RULES


@pytest.mark.parametrize("rule", RULES)
def test_the_good_tree_passes(tmp_path, rule):
    code, report = run(tmp_path, rule)
    assert report["findings"] == []
    assert code == 0


@pytest.mark.parametrize("rule", RULES)
def test_the_base_tree_passes(tmp_path, rule):
    write_project(tmp_path)
    code, report = check_json(tmp_path, "--rule", rule)
    assert report["findings"] == []
    assert code == 0


def test_asy_01_a_module_level_client_and_a_manager_building_an_impl(tmp_path):
    files = {
        f"{API}/clients.py": (
            "import boto3\nfrom acme.infra.cache.memory import CacheMemoryImpl\n\n"
            "S3 = boto3.client('s3')\nCACHE = CacheMemoryImpl()\n"
        ),
        MANAGER: "from acme.infra.cache.memory import CacheMemoryImpl\n\n\ndef f():\n    return CacheMemoryImpl()\n",
    }
    code, report = run(tmp_path, "ASY-01", files)
    assert code == 1
    assert [(p, line) for _, p, line in rules_found(report)] == [(MANAGER, 5), (f"{API}/clients.py", 4), (f"{API}/clients.py", 5)]


def test_asy_02_a_missing_getter_and_an_impl_imported_outside_boot(tmp_path):
    files = edit(ROOT, "    @abstractmethod\n    def get_topics(self) -> TopicsInterface: ...\n\n", "")
    files[f"{API}/routers/files.py"] = (
        "from acme.infra.buckets.local import BucketsLocalImpl\nfrom acme.infra.buckets import Buckets\n"
    )
    code, report = run(tmp_path, "ASY-02", files)
    assert code == 1
    assert messages(report) == [
        "InfraInterface has no getter returning TopicsInterface (topics)",
        "acme.services.api.routers.files imports acme.infra.buckets.local; only boot modules pick an infra impl",
    ]


def test_asy_02_the_boot_modules_come_from_the_options(tmp_path):
    files = {f"{API}/wiring.py": "from acme.infra.impl import local\n"}
    pyproject = '[tool.arch-check]\npackage = "acme"\n\n[tool.arch-check.options.ASY-02]\nboot = ["*.container", "*.wiring"]\n'
    code, _ = run(tmp_path, "ASY-02", files, pyproject)
    assert code == 0


def test_asy_03_an_impl_without_describe(tmp_path):
    files = edit(f"{INFRA}/topics/memory.py", '    def describe(self) -> str:\n        return "topics=memory"\n', "    pass\n")
    files.update(edit(f"{INFRA}/secrets/__init__.py", "    @abstractmethod\n    def describe(self) -> str: ...\n", ""))
    code, report = run(tmp_path, "ASY-03", files)
    assert code == 1
    assert messages(report) == ["TopicsMemoryImpl has no describe(); the boot inventory line reads it"]


def test_asy_04_a_string_scope_and_a_manager_asking_for_a_cache(tmp_path):
    files = edit(CONTAINER, "get_cache(CacheScope.RATE_LIMIT)", 'get_cache("rate_limit")')
    files[MANAGER] = "def f(infra, scope):\n    return infra.get_cache(scope)\n"
    code, report = run(tmp_path, "ASY-04", files)
    assert code == 1
    assert messages(report) == [
        "a manager calls get_cache; it receives its cache already scoped",
        "get_cache takes a CacheScope member, never a free string",
    ]


def test_asy_04_a_scope_that_is_not_an_enum(tmp_path):
    code, report = run(tmp_path, "ASY-04", edit(CACHE, "class CacheScope(str, Enum):", "class CacheScope:"))
    assert code == 1
    assert messages(report) == ["CacheScope is not an Enum; a scope is a fixed member"]


def test_asy_07_storage_reaching_a_cache(tmp_path):
    files = {
        f"{OM}/orders/storage/impl/postgres.py": "from acme.infra.cache import CacheInterface\n",
        f"{OM}/orders/caching.py": (
            "class CachedOrders(OrdersStorageInterface):\n"
            "    def __init__(self, cache: CacheInterface):\n"
            "        self._cache = cache\n"
        ),
    }
    code, report = run(tmp_path, "ASY-07", files)
    assert code == 1
    assert sorted(p for _, p, _ in rules_found(report)) == sorted(files)


def test_asy_08_a_string_bucket_and_an_untyped_parameter(tmp_path):
    files = edit(BUCKETS, "async def get(self, org_id, bucket: Buckets,", "async def get(self, org_id, bucket: str,")
    files[f"{API}/routers/files.py"] = 'async def f(b):\n    return await b.get(org_id, bucket="exports", key="k")\n'
    code, report = run(tmp_path, "ASY-08", files)
    assert code == 1
    assert messages(report) == [
        "BucketsInterface.get takes bucket as str; it takes Buckets",
        "a bucket named by a string; name it by a Buckets member",
    ]


def test_asy_08_no_local_impl(tmp_path):
    files = edit(f"{INFRA}/buckets/local.py", "BucketsLocalImpl", "BucketsS3Impl")
    files[f"{INFRA}/buckets/local.py"] = "import boto3\n\n\n" + files[f"{INFRA}/buckets/local.py"]
    code, report = run(tmp_path, "ASY-08", files)
    assert code == 1
    assert messages(report) == ["no local impl subclasses BucketsInterface; tests run without the cloud"]


def test_asy_09_every_shape_of_the_topics_package(tmp_path):
    text = (
        GOOD[TOPICS]
        .replace('extra="ignore"', 'extra="forbid"')
        .replace("    produced_at: datetime\n", "")
        .replace("class WorkAvailablePayload(TopicPayload):", "class WorkAvailablePayload(BaseModel):")
        .replace("payload: TopicPayload) -> None", "payload: TopicPayload) -> str")
        .replace("consumer: str, handler) -> Callable[[], None]", "handler) -> None")
    )
    files = {TOPICS: text, f"{API}/routers/wake.py": 'async def f(topics, p):\n    await topics.publish("work_available", p)\n'}
    code, report = run(tmp_path, "ASY-09", files)
    assert code == 1
    assert messages(report) == [
        "TopicPayload declares no produced_at",
        'TopicPayload does not set extra="ignore"; an old consumer must read a new payload',
        "WorkAvailablePayload does not extend TopicPayload",
        "a topic published by its string name; publish a Topics member",
        "publish returns something; it returns None",
        "subscribe returns no unsubscribe callable",
        "subscribe takes no consumer name",
    ]


def test_asy_09_a_payload_base_on_the_om_root(tmp_path):
    code, report = run(tmp_path, "ASY-09", edit(TOPICS, "class TopicPayload(BaseModel):", "class TopicPayload(Platform):"))
    assert code == 1
    assert messages(report) == ["TopicPayload extends pydantic's BaseModel directly, never the OM root"]


def test_asy_15_a_service_spawning_a_task_or_a_thread(tmp_path):
    files = {
        f"{API}/routers/export.py": (
            "import asyncio\nfrom threading import Thread\nfrom fastapi import BackgroundTasks\n\n\n"
            "async def f(job, tasks: BackgroundTasks):\n    asyncio.create_task(job())\n    Thread(target=job).start()\n"
        ),
        f"{API}/sweep.py": "from apscheduler.schedulers.asyncio import AsyncIOScheduler\n",
    }
    code, report = run(tmp_path, "ASY-15", files)
    assert code == 1
    assert len(report["findings"]) == 4


def test_asy_15_the_edge_comes_from_the_options(tmp_path):
    files = {f"{API}/sockets/hub.py": "import asyncio\n\n\ndef f(c):\n    return asyncio.create_task(c)\n"}
    code, _ = run(tmp_path, "ASY-15", files)
    assert code == 1
    pyproject = (
        '[tool.arch-check]\npackage = "acme"\n\n[tool.arch-check.options.ASY-15]\nedge = ["*.realtime.*", "*.sockets.*"]\n'
    )
    code, _ = run(tmp_path, "ASY-15", files, pyproject)
    assert code == 0


def test_asy_16_a_work_item_without_a_lease_and_a_table_without_the_unique_key(tmp_path):
    files = edit(WORK, "    lease_expires_at: datetime | None\n", "")
    files[WORK] = files[WORK].replace("WORK_PAYLOADS = {WorkKind.NOOP: NoopPayload}\n", "")
    files.update(edit(WORK_TABLE, ", unique=True", ""))
    code, report = run(tmp_path, "ASY-16", files)
    assert code == 1
    assert messages(report) == [
        "WorkItem declares no lease_expires_at",
        "WorkItems has no unique index on idempotency_key",
        "acme.om.work declares no WORK_PAYLOADS dict literal",
    ]


def test_asy_19_a_scheduled_rule_and_a_scheduler_library(tmp_path):
    files = {
        "deployment/terraform/sweep.tf": (
            'resource "aws_cloudwatch_event_rule" "sweep" {\n  schedule_expression = "rate(5 minutes)"\n}\n'
        ),
        "deployment/terraform/cron.tf": 'resource "aws_scheduler_schedule" "nightly" {\n  name = "x"\n}\n',
        f"{OM}/orders/sweep.py": "import rq_scheduler\n",
    }
    code, report = run(tmp_path, "ASY-19", files)
    assert code == 1
    assert sorted(p for _, p, _ in rules_found(report)) == sorted(files)


def test_asy_28_a_secrets_file_read_without_a_mode_check(tmp_path):
    files = edit(
        SECRETS_LOCAL,
        (
            "        mode = stat.S_IMODE(self._file.stat().st_mode)\n"
            "        if mode & 0o077:\n"
            "            raise PermissionError(self._file)\n"
        ),
        "",
    )
    code, report = run(tmp_path, "ASY-28", files)
    assert code == 1
    assert messages(report) == ["_read reads a file with no owner-only mode check before it"]


def test_asy_29_a_trace_id_and_a_missing_traceparent(tmp_path):
    files = edit(OUTBOX, "    traceparent: str | None = None\n", "    trace_id: str | None = None\n")
    code, report = run(tmp_path, "ASY-29", files)
    assert code == 1
    assert messages(report) == ["OutboxRow carries trace_id; it carries the traceparent", "OutboxRow declares no traceparent"]


def test_asy_01_an_http_client_and_an_impl_from_outside_infra_pass(tmp_path):
    files = {
        f"{API}/clients.py": (
            "import httpx\nfrom acme.services.api.impl.orders import OrdersServiceImpl\n\n"
            "HTTP = httpx.AsyncClient()\nORDERS = OrdersServiceImpl()\n"
        )
    }
    code, report = run(tmp_path, "ASY-01", files)
    assert report["findings"] == []
    assert code == 0


def test_asy_02_the_root_in_infra_impl_and_a_helper_module(tmp_path):
    files = {ROOT: None, f"{INFRA}/impl/root.py": GOOD[ROOT]}
    files[f"{INFRA}/topics/dispatch.py"] = "class LocalSubscribers:\n    pass\n"
    files[f"{API}/routers/wake.py"] = "from acme.infra.topics.dispatch import LocalSubscribers\n"
    code, report = run(tmp_path, "ASY-02", files)
    assert report["findings"] == []
    assert code == 0
    files[f"{INFRA}/impl/root.py"] = GOOD[ROOT].replace("    @abstractmethod\n    async def start(self) -> None: ...\n\n", "")
    code, report = run(tmp_path, "ASY-02", files)
    assert code == 1
    assert messages(report) == ["InfraInterface declares no start()"]


def test_asy_03_an_interface_without_describe_passes(tmp_path):
    files = edit(f"{INFRA}/secrets/__init__.py", "    @abstractmethod\n    def describe(self) -> str: ...\n", "")
    code, report = run(tmp_path, "ASY-03", files)
    assert report["findings"] == []
    assert code == 0


def test_asy_04_a_scope_from_a_loop_passes_and_an_f_string_fails(tmp_path):
    files = edit(
        CONTAINER,
        "    return infra.get_cache(CacheScope.RATE_LIMIT)\n",
        "    return [infra.get_cache(s) for s in CacheScope]\n",
    )
    code, report = run(tmp_path, "ASY-04", files)
    assert report["findings"] == []
    assert code == 0
    files[CONTAINER] = files[CONTAINER].replace("infra.get_cache(s)", 'infra.get_cache(f"{s}")')
    code, report = run(tmp_path, "ASY-04", files)
    assert code == 1
    assert messages(report) == ["get_cache takes a CacheScope member, never a free string"]


def test_asy_04_a_scope_declared_in_a_submodule_and_re_exported(tmp_path):
    scope = 'from enum import Enum\n\n\nclass CacheScope(str, Enum):\n    RATE_LIMIT = "rate_limit"\n'
    init = GOOD[CACHE].replace('class CacheScope(str, Enum):\n    RATE_LIMIT = "rate_limit"\n\n\n', "")
    files = {CACHE: "from .scopes import CacheScope\n" + init, f"{INFRA}/cache/scopes.py": scope}
    code, report = run(tmp_path, "ASY-04", files)
    assert report["findings"] == []
    assert code == 0
    files[f"{INFRA}/cache/scopes.py"] = scope.replace("(str, Enum)", "")
    code, report = run(tmp_path, "ASY-04", files)
    assert code == 1
    assert rules_found(report) == [("ASY-04", f"{INFRA}/cache/scopes.py", 4)]


def test_asy_08_a_filesystem_impl_and_a_string_bucket_inside_infra_pass(tmp_path):
    files = edit(f"{INFRA}/buckets/local.py", "BucketsLocalImpl", "FilesystemBucketsImpl")
    files[f"{INFRA}/buckets/s3.py"] = (
        "import boto3\n\n\nclass BucketsS3Impl(BucketsInterface):\n"
        '    def describe(self) -> str:\n        return self._client.head(bucket="exports")\n'
    )
    code, report = run(tmp_path, "ASY-08", files)
    assert report["findings"] == []
    assert code == 0


def test_asy_08_the_enum_declared_in_a_submodule(tmp_path):
    enum = 'from enum import Enum\n\n\nclass Buckets(str, Enum):\n    EXPORTS = "exports"\n'
    init = GOOD[BUCKETS].replace('class Buckets(str, Enum):\n    EXPORTS = "exports"\n\n\n', "")
    files = {BUCKETS: "from acme.infra.buckets.names import Buckets\n" + init, f"{INFRA}/buckets/names.py": enum}
    code, report = run(tmp_path, "ASY-08", files)
    assert report["findings"] == []
    assert code == 0
    files[f"{INFRA}/buckets/names.py"] = enum.replace("(str, Enum)", "")
    code, report = run(tmp_path, "ASY-08", files)
    assert code == 1
    assert messages(report) == ["Buckets is not an Enum in the buckets package; a bucket is a fixed member"]


def test_asy_09_the_payloads_in_a_submodule_and_a_string_publish_inside_infra(tmp_path):
    head, _, tail = GOOD[TOPICS].partition("class TopicsInterface(ABC):")
    files = {
        f"{INFRA}/topics/payloads.py": head,
        TOPICS: "from abc import ABC, abstractmethod\nfrom collections.abc import Callable\n\nfrom .payloads import *\n\n\n"
        "class TopicsInterface(ABC):" + tail,
        f"{INFRA}/topics/valkey.py": 'async def relay(bus, p):\n    await bus.publish("work_available", p)\n',
    }
    code, report = run(tmp_path, "ASY-09", files)
    assert report["findings"] == []
    assert code == 0
    files[f"{INFRA}/topics/payloads.py"] = head.replace("    produced_at: datetime\n", "")
    code, report = run(tmp_path, "ASY-09", files)
    assert code == 1
    assert rules_found(report) == [("ASY-09", f"{INFRA}/topics/payloads.py", 8)]


def test_asy_15_a_task_group_passes(tmp_path):
    files = {
        f"{API}/routers/export.py": (
            "import asyncio\n\n\nasync def f(a, b):\n    async with asyncio.TaskGroup() as g:\n"
            "        g.create_task(a())\n        g.create_task(b())\n"
        )
    }
    code, report = run(tmp_path, "ASY-15", files)
    assert report["findings"] == []
    assert code == 0


def test_asy_15_a_loop_or_an_executor_spawning(tmp_path):
    files = {
        f"{API}/routers/export.py": (
            "import asyncio\nfrom concurrent.futures import ThreadPoolExecutor\n\n\n"
            "POOL = ThreadPoolExecutor()\n\n\n"
            "async def f(job):\n"
            "    asyncio.get_running_loop().create_task(job())\n"
            "    loop = asyncio.get_event_loop()\n"
            "    loop.create_task(job())\n"
            "    await loop.run_in_executor(None, job)\n"
            "    ThreadPoolExecutor().submit(job)\n"
            "    POOL.submit(job)\n"
        )
    }
    code, report = run(tmp_path, "ASY-15", files)
    assert code == 1
    assert len(report["findings"]) == 5


def test_asy_15_an_executor_a_with_holds_passes(tmp_path):
    files = {
        f"{API}/routers/export.py": (
            "from concurrent.futures import ThreadPoolExecutor\n\n\n"
            "def f(job, jobs):\n    with ThreadPoolExecutor() as pool:\n        pool.submit(job)\n        jobs.submit(job)\n"
        )
    }
    code, report = run(tmp_path, "ASY-15", files)
    assert report["findings"] == []
    assert code == 0


def test_asy_16_the_payload_map_elsewhere_and_a_unique_constraint(tmp_path):
    files = edit(WORK, "WORK_PAYLOADS = {WorkKind.NOOP: NoopPayload}\n", "")
    files[f"{OM}/work/kinds.py"] = "WORK_PAYLOADS = {WorkKind.NOOP: NoopPayload}\n"
    files.update(
        edit(
            WORK_TABLE,
            'Index("uq_work_items_idempotency_key", "idempotency_key", unique=True)',
            'UniqueConstraint("idempotency_key")',
        )
    )
    code, report = run(tmp_path, "ASY-16", files)
    assert report["findings"] == []
    assert code == 0


def test_asy_19_a_job_queue_is_not_a_scheduler(tmp_path):
    code, report = run(tmp_path, "ASY-19", {f"{OM}/orders/jobs.py": "import celery\nimport rq\n"})
    assert report["findings"] == []
    assert code == 0


def test_asy_28_a_helper_an_equality_and_a_write(tmp_path):
    files = {
        SECRETS_LOCAL: """\
import stat


class SecretsLocalImpl(SecretsInterface):
    def describe(self) -> str:
        return "secrets=local"

    def _assert_owner_only(self) -> None:
        if stat.S_IMODE(self._file.stat().st_mode) != 0o600:
            raise PermissionError(self._file)

    def _read(self) -> str:
        self._assert_owner_only()
        return self._file.read_text()

    def put(self, text: str) -> None:
        with open(self._file, "w") as f:
            f.write(text)
"""
    }
    code, report = run(tmp_path, "ASY-28", files)
    assert report["findings"] == []
    assert code == 0
    files[SECRETS_LOCAL] = files[SECRETS_LOCAL].replace("        self._assert_owner_only()\n", "")
    code, report = run(tmp_path, "ASY-28", files)
    assert code == 1
    assert messages(report) == ["_read reads a file with no owner-only mode check before it"]


# --- review fixes


@pytest.mark.parametrize(
    "source",
    [
        "from acme.infra.cache import memory\n\n\ndef f():\n    return memory.CacheMemoryImpl()\n",
        "import acme.infra.cache.memory as m\n\n\ndef f():\n    return m.CacheMemoryImpl()\n",
    ],
)
def test_asy_01_a_manager_building_an_impl_through_its_module(tmp_path, source):
    code, report = run(tmp_path, "ASY-01", {MANAGER: source})
    assert code == 1
    assert [(p, line) for _, p, line in rules_found(report)] == [(MANAGER, 5)]


def test_asy_19_a_brace_in_a_string_or_a_comment_does_not_end_the_block(tmp_path):
    files = {
        "deployment/terraform/a.tf": (
            'resource "aws_cloudwatch_event_rule" "a" {\n  description = "fires at } midnight"\n'
            '  schedule_expression = "cron(0 0 * * ? *)"\n}\n'
        ),
        "deployment/terraform/b.tf": (
            'resource "aws_cloudwatch_event_rule" "b" {\n  # a comment with } in it\n'
            '  /* and } here */\n  schedule_expression = "rate(1 day)"\n}\n'
        ),
        "deployment/terraform/c.tf": (
            'resource "aws_cloudwatch_event_rule" "c" {\n  event_pattern = "{}"\n}\nlocals {\n  schedule_expression = "x"\n}\n'
        ),
    }
    code, report = run(tmp_path, "ASY-19", files)
    assert code == 1
    assert sorted(p for _, p, _ in rules_found(report)) == ["deployment/terraform/a.tf", "deployment/terraform/b.tf"]


@pytest.mark.parametrize(
    "read",
    [
        "with self._file.open() as f:\n            return f.read()",
        "with open(self._file, mode=self._mode) as f:\n            return f.read()",
    ],
)
def test_asy_28_a_path_open_or_an_unread_mode_is_a_read(tmp_path, read):
    source = (
        "class SecretsLocalImpl(SecretsInterface):\n    def describe(self) -> str:\n        return 'secrets=local'\n\n"
        f"    def _read(self) -> str:\n        {read}\n"
    )
    code, report = run(tmp_path, "ASY-28", {SECRETS_LOCAL: source})
    assert code == 1
    assert messages(report) == ["_read reads a file with no owner-only mode check before it"]
    code, report = run(tmp_path, "ASY-28", {SECRETS_LOCAL: source.replace("self._file.open()", "self._file.open('w')")})
    assert code == (0 if "open()" in read else 1)
