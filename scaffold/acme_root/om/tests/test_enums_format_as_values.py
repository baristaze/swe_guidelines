"""Every enum the platform formats into a key, a name, a log line, or a
message formats as its value. A `(str, Enum)` member formats as `Class.MEMBER`
on Python 3.12 and later, so an f-string built from one names the class
instead of the value; every one of them is a `StrEnum`."""

import enum

import pytest

from acme.infra.buckets import Buckets
from acme.infra.cache import CacheScope
from acme.infra.queues import Queues
from acme.infra.topics import Topics
from acme.om.context import (
    AppType,
    CredentialKind,
    OperatorPermission,
    OperatorRole,
    Permission,
    Role,
)
from acme.om.media.types.file import FilePurpose, FileStatus
from acme.om.orchestrations.types.orchestration import OrchestrationKind, OrchestrationStatus
from acme.om.storage.roles import DatabaseRole
from acme.om.storage.scopes import ScopeKind
from acme.om.work.types.work_item import WorkKind, WorkStatus

ENUMS = [
    Buckets,
    CacheScope,
    Queues,
    Topics,
    AppType,
    CredentialKind,
    OperatorPermission,
    OperatorRole,
    Permission,
    Role,
    DatabaseRole,
    ScopeKind,
    FilePurpose,
    FileStatus,
    OrchestrationKind,
    OrchestrationStatus,
    WorkKind,
    WorkStatus,
]


@pytest.mark.parametrize("kind", ENUMS, ids=lambda kind: kind.__name__)
def test_a_member_formats_as_its_value(kind: type[enum.Enum]) -> None:
    assert issubclass(kind, enum.StrEnum)
    for member in kind:
        assert f"{member}" == member.value
        assert str(member) == member.value
