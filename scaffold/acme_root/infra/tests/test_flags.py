"""The flags contract, over the memory impl and the OpenFeature impl on
LaunchDarkly's provider with the SDK's offline test data: one set of rules
reads the same in both. A user's rule wins over its org's, an org's over
the provider's default, and that over the code's; a flag the provider does
not know, or a provider that fails, reads the code's default."""

import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from ldclient.config import Config
from ldclient.integrations.test_data import TestData
from openfeature.evaluation_context import EvaluationContext
from openfeature.flag_evaluation import FlagResolutionDetails
from openfeature.provider import AbstractProvider, Metadata

from acme.infra.base import new_id
from acme.infra.flags import FLAGS, Flag, FlagSet, FlagsInterface, code_defaults
from acme.infra.flags.defaults import FlagsDefaultsImpl
from acme.infra.flags.launchdarkly import launchdarkly_flags
from acme.infra.flags.memory import FlagRule, FlagsMemoryImpl
from acme.infra.flags.openfeature import FlagsOpenFeatureImpl

FLAG = Flag.MEDIA_UPLOADS
DEFAULT = FLAGS[FLAG].default
SDK_KEY = "sdk-7f3a9c1e-flags-test-key"

ORG_A = new_id()
ORG_B = new_id()
USER_IN_A = new_id()
OTHER_USER_IN_A = new_id()


@dataclass
class Rules:
    """One flag's rules, as both impls take them: the provider's default,
    then a value for an org, then one for a user."""

    provider_default: bool
    orgs: dict[UUID, bool] = field(default_factory=dict[UUID, bool])
    users: dict[UUID, bool] = field(default_factory=dict[UUID, bool])


def offline_config(data: TestData) -> Config:
    """No call leaves the process: the rules come from the test data, and no
    event or diagnostic is sent."""
    return Config(SDK_KEY, update_processor_class=data, send_events=False, diagnostic_opt_out=True)


def launchdarkly_data(rules: Rules | None) -> TestData:
    """The rules in LaunchDarkly's terms. A user's rule sits above an org's,
    since rules match in order; the provider's default is the fallthrough."""
    data = TestData.data_source()
    if rules is None:
        return data
    flag = data.flag(FLAG.value).boolean_flag().fallthrough_variation(rules.provider_default)
    for user_id, value in rules.users.items():
        flag = flag.if_match_context("org", "user_id", str(user_id)).then_return(value)
    for org_id, value in rules.orgs.items():
        flag = flag.if_match_context("org", "org_id", str(org_id)).then_return(value)
    data.update(flag)
    return data


def memory(rules: Rules | None) -> FlagsInterface:
    if rules is None:
        return FlagsMemoryImpl({})
    rule = FlagRule(default=rules.provider_default, orgs=rules.orgs, users=rules.users)
    return FlagsMemoryImpl({FLAG.value: rule})


def openfeature(rules: Rules | None) -> FlagsInterface:
    return launchdarkly_flags(offline_config(launchdarkly_data(rules)), timedelta(seconds=1))


IMPLS: dict[str, Callable[[Rules | None], FlagsInterface]] = {
    "memory": memory,
    "openfeature": openfeature,
}


Build = Callable[[Rules | None], Awaitable[FlagsInterface]]


@pytest.fixture(params=sorted(IMPLS))
async def flags_over(request: pytest.FixtureRequest) -> AsyncIterator[Build]:
    """Each impl over the rules a test gives, started, and closed after it."""
    started: list[FlagsInterface] = []

    async def build(rules: Rules | None) -> FlagsInterface:
        flags = IMPLS[request.param](rules)
        await flags.start()
        started.append(flags)
        return flags

    yield build
    for flags in started:
        await flags.close()


async def value(flags: FlagsInterface, org_id: UUID, user_id: UUID | None = None) -> bool:
    return (await flags.evaluate(org_id, user_id)).on(FLAG)


async def test_a_users_rule_wins_over_its_orgs_and_an_orgs_over_the_providers_default(
    flags_over: Build,
) -> None:
    flags = await flags_over(
        Rules(
            provider_default=not DEFAULT,
            orgs={ORG_A: DEFAULT},
            users={USER_IN_A: not DEFAULT},
        )
    )
    assert await value(flags, ORG_A, USER_IN_A) is (not DEFAULT)  # the user's rule
    assert await value(flags, ORG_A, OTHER_USER_IN_A) is DEFAULT  # the org's rule
    assert await value(flags, ORG_A) is DEFAULT  # the org's rule, no user
    assert await value(flags, ORG_B) is (not DEFAULT)  # the provider's default
    assert await value(flags, ORG_B, new_id()) is (not DEFAULT)


async def test_a_flag_the_provider_does_not_know_reads_the_codes_default(
    flags_over: Build,
) -> None:
    flags = await flags_over(None)
    assert await flags.evaluate(ORG_A, USER_IN_A) == code_defaults()


class FlakyProvider(AbstractProvider):
    """A provider that answers the opposite of every default it is given,
    and raises once it is down: a vendor's outage, or a defect in its SDK,
    seen from the caller's side."""

    def __init__(self) -> None:
        self.down = False

    def get_metadata(self) -> Metadata:
        return Metadata(name="flaky")

    def resolve_boolean_details(
        self,
        flag_key: str,
        default_value: bool,
        evaluation_context: EvaluationContext | None = None,
    ) -> FlagResolutionDetails[bool]:
        if self.down:
            raise RuntimeError("the provider is down")
        return FlagResolutionDetails(value=not default_value)

    def resolve_string_details(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def resolve_integer_details(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def resolve_float_details(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def resolve_object_details(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError


async def test_a_failing_provider_reads_the_codes_default_in_the_openfeature_impl() -> None:
    provider = FlakyProvider()
    flags = FlagsOpenFeatureImpl("flaky", lambda: provider)
    await flags.start()
    assert await value(flags, ORG_A, USER_IN_A) is (not DEFAULT)
    provider.down = True
    assert await flags.evaluate(ORG_A, USER_IN_A) == code_defaults()
    await flags.close()


async def test_a_failing_provider_reads_the_codes_default_in_the_memory_impl(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The file says the opposite of every default, then cannot be read."""
    file = tmp_path / "flags.json"
    flags = FlagsMemoryImpl(file=file)
    file.write_text(json.dumps({FLAG.value: {"default": not DEFAULT}}))
    assert await value(flags, ORG_A) is (not DEFAULT)
    file.write_text("{not json")
    assert await flags.evaluate(ORG_A, USER_IN_A) == code_defaults()
    assert f"flags file {file} unreadable" in caplog.text


async def test_a_missing_rules_file_holds_no_rules(tmp_path: Path) -> None:
    flags = FlagsMemoryImpl(file=tmp_path / "flags.json")
    assert await flags.evaluate(ORG_A) == code_defaults()


async def test_no_provider_reads_every_default() -> None:
    flags = FlagsDefaultsImpl()
    assert await flags.evaluate(ORG_A, USER_IN_A) == code_defaults()
    assert flags.describe() == "flags=none(every flag reads its default)"


def test_a_client_reads_only_the_flags_marked_for_clients() -> None:
    values = FlagSet(values={flag: True for flag in Flag})
    assert set(values.for_clients()) == {flag for flag in Flag if FLAGS[flag].client}


def test_every_flag_is_declared_with_its_default_and_its_mark() -> None:
    assert set(FLAGS) == set(Flag)


async def test_launchdarkly_evaluates_an_org_target_and_a_user_rule_and_never_writes_its_key(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    data = TestData.data_source()
    data.update(
        data.flag(FLAG.value)
        .boolean_flag()
        .fallthrough_variation(True)
        .variation_for_key("org", str(ORG_A), False)
        .if_match_context("org", "user_id", str(USER_IN_A))
        .then_return(False)
    )
    flags = launchdarkly_flags(offline_config(data), timedelta(seconds=1))
    await flags.start()
    assert await value(flags, ORG_A) is False  # the org's target
    assert await value(flags, ORG_B, USER_IN_A) is False  # the rule on user_id
    assert await value(flags, ORG_B, new_id()) is True  # the fallthrough
    await flags.close()
    assert flags.describe() == "flags=launchdarkly(openfeature)"
    assert caplog.records, "the SDK wrote no line, so the check below checked nothing"
    lines = [record.getMessage() for record in caplog.records]
    assert not [line for line in [flags.describe(), *lines] if SDK_KEY in line]
