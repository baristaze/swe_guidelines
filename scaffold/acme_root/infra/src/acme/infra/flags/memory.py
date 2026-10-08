import asyncio
import json
import logging
from collections.abc import Mapping
from pathlib import Path
from uuid import UUID

from pydantic import Field, TypeAdapter, ValidationError

from acme.infra.base import InfraModel
from acme.infra.flags import FLAGS, FlagSet, FlagsInterface, code_defaults

log = logging.getLogger(__name__)


class FlagRule(InfraModel):
    """One flag's rules: the provider's default, then a value for an org and
    a value for a user, which wins over its org's."""

    default: bool | None = None
    orgs: dict[UUID, bool] = Field(default_factory=dict)
    users: dict[UUID, bool] = Field(default_factory=dict)

    def value_for(self, org_id: UUID, user_id: UUID | None, code_default: bool) -> bool:
        if user_id is not None and user_id in self.users:
            return self.users[user_id]
        if org_id in self.orgs:
            return self.orgs[org_id]
        if self.default is not None:
            return self.default
        return code_default


RULES = TypeAdapter(dict[str, FlagRule])
"""A rules file: a JSON object from a flag's name to its rules. A name no
flag declares is ignored, as a provider ignores a flag no code reads."""


def evaluate_rules(rules: Mapping[str, FlagRule], org_id: UUID, user_id: UUID | None) -> FlagSet:
    values = {}
    for flag, spec in FLAGS.items():
        rule = rules.get(flag.value)
        values[flag] = (
            spec.default if rule is None else rule.value_for(org_id, user_id, spec.default)
        )
    return FlagSet(values=values)


class FlagsMemoryImpl(FlagsInterface):
    """The rules given, or the rules of a file read on every evaluation, so a
    developer's edit applies without a restart. A missing file holds no
    rules. A file that cannot be read is a provider that fails: every flag
    reads its default, and a warning names the file. Local and tests only:
    a deployed environment refuses it at boot."""

    def __init__(
        self, rules: Mapping[str, FlagRule] | None = None, *, file: Path | None = None
    ) -> None:
        self._rules = dict(rules or {})
        self._file = file

    async def evaluate(self, org_id: UUID, user_id: UUID | None = None) -> FlagSet:
        if self._file is None:
            return evaluate_rules(self._rules, org_id, user_id)
        try:
            rules = await asyncio.to_thread(self._read_file, self._file)
        except (OSError, ValueError, ValidationError) as error:
            log.warning(
                "flags file %s unreadable (%s); every flag reads its default",
                self._file,
                type(error).__name__,
            )
            return code_defaults()
        return evaluate_rules(rules, org_id, user_id)

    @staticmethod
    def _read_file(file: Path) -> dict[str, FlagRule]:
        if not file.exists():
            return {}
        return RULES.validate_python(json.loads(file.read_text()))

    def describe(self) -> str:
        return f"flags=memory({self._file or 'rules in process'})"

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None
