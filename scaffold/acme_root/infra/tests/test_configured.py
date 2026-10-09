from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

from acme.infra.base import new_id
from acme.infra.cache import CacheScope
from acme.infra.cache.breaker import CacheBreakerImpl
from acme.infra.flags import code_defaults
from acme.infra.impl.configured import InfraConfiguredImpl, UnsafeConfiguration
from acme.infra.impl.settings import InfraSettings
from acme.infra.topics.breaker import TopicsBreakerImpl

CLOUD_BACKENDS = {
    "secrets_backend": "aws",
    "cache_backend": "valkey",
    "topics_backend": "valkey",
    "buckets_backend": "s3",
    "queues_backend": "sqs",
    "flags_backend": "none",
}

SDK_KEY = "sdk-0b1d4c2e-flags-test-key"


@pytest.fixture(autouse=True)
def _away_from_the_checkout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The settings read `.env` from the working directory, and the
    checkout's, which `make up` writes, names the compose stack; a test
    builds its settings from its own values alone."""
    monkeypatch.chdir(tmp_path)


def local_settings(tmp_path: Path, **overrides: object) -> InfraSettings:
    base = {
        "environment": "local",
        "buckets_root": tmp_path / "buckets",
        "secrets_file": tmp_path / "secrets.env",
        "flags_file": tmp_path / "flags.json",
    }
    return InfraSettings.model_validate({**base, **overrides})


@pytest.mark.parametrize("environment", ["dev", "staging", "production"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("secrets_backend", "local"),
        ("cache_backend", "memory"),
        ("topics_backend", "memory"),
        ("buckets_backend", "local"),
        ("queues_backend", "memory"),
        ("flags_backend", "memory"),
    ],
)
def test_deployed_environments_refuse_local_backends(
    tmp_path: Path, environment: str, field: str, value: str
) -> None:
    with pytest.raises(UnsafeConfiguration) as raised:
        InfraConfiguredImpl(
            local_settings(tmp_path, environment=environment, **{**CLOUD_BACKENDS, field: value})
        )
    assert field.upper() in raised.value.message


@pytest.mark.parametrize("environment", ["prod", "Production", "stage", ""])
def test_an_unknown_environment_name_is_refused(tmp_path: Path, environment: str) -> None:
    """Terraform passes `production`; a process named `prod` must not boot on
    the file secrets backend by slipping past the cloud checks."""
    with pytest.raises(UnsafeConfiguration) as raised:
        InfraConfiguredImpl(local_settings(tmp_path, environment=environment))
    assert "ACME_ENVIRONMENT" in raised.value.message


async def test_local_environment_builds_local_impls(tmp_path: Path) -> None:
    infra = InfraConfiguredImpl(local_settings(tmp_path))
    assert infra.get_cache(CacheScope.RATE_LIMIT) is infra.get_cache(CacheScope.RATE_LIMIT)
    assert infra.describe() == [
        *(f"cache[{scope.value}]=memory" for scope in CacheScope),
        "outages=none",
        "topics=memory",
        f"buckets=local({tmp_path / 'buckets'})",
        "queues=memory",
        f"secrets=local({tmp_path / 'secrets.env'})",
        f"flags=memory({tmp_path / 'flags.json'})",
    ]
    await infra.start()
    await infra.close()


def test_cloud_backends_are_constructed_without_connecting(tmp_path: Path) -> None:
    infra = InfraConfiguredImpl(local_settings(tmp_path, **CLOUD_BACKENDS))
    assert infra.describe() == [
        *(f"cache[{scope.value}]=valkey+breaker(3/30s)" for scope in CacheScope),
        "outages=shared(cache[outage]=valkey+breaker(3/30s))",
        "topics=valkey+breaker(3/30s)",
        "buckets=s3(us-east-1)",
        "queues=sqs(us-east-1)",
        "secrets=aws(us-east-1)",
        "flags=none(every flag reads its default)",
    ]
    assert (
        infra.get_cache(CacheScope.NETWORK_RESPONSE).describe()
        == "cache[network_response]=valkey+breaker(3/30s)"
    )


@pytest.mark.parametrize("environment", ["local", "staging"])
@pytest.mark.parametrize("key", [None, "", "off"])
def test_launchdarkly_without_its_key_is_refused(
    tmp_path: Path, environment: str, key: str | None
) -> None:
    """A process that asked for rules never runs on none in silence."""
    settings = local_settings(
        tmp_path,
        environment=environment,
        **{**CLOUD_BACKENDS, "flags_backend": "launchdarkly"},
        launchdarkly_sdk_key=key,
    )
    with pytest.raises(UnsafeConfiguration) as raised:
        InfraConfiguredImpl(settings)
    assert "ACME_LAUNCHDARKLY_SDK_KEY" in raised.value.message


async def test_no_flag_backend_boots_on_the_codes_defaults_and_says_so(tmp_path: Path) -> None:
    infra = InfraConfiguredImpl(local_settings(tmp_path, environment="staging", **CLOUD_BACKENDS))
    assert infra.describe()[-1] == "flags=none(every flag reads its default)"
    assert await infra.get_flags().evaluate(new_id(), new_id()) == code_defaults()


def test_the_launchdarkly_key_is_in_no_boot_line_and_no_repr(tmp_path: Path) -> None:
    settings = local_settings(
        tmp_path,
        environment="staging",
        **{**CLOUD_BACKENDS, "flags_backend": "launchdarkly"},
        launchdarkly_sdk_key=SDK_KEY,
    )
    infra = InfraConfiguredImpl(settings)
    assert infra.describe()[-1] == "flags=launchdarkly(openfeature)"
    assert not [line for line in infra.describe() if SDK_KEY in line]
    assert SDK_KEY not in repr(settings)


def test_the_breaker_wraps_the_out_of_process_impls_and_only_those(tmp_path: Path) -> None:
    """The memory and in-process impls cannot time out, so they are handed out
    bare; the Valkey impls are what a breaker in front of them protects the
    pool from."""
    memory = InfraConfiguredImpl(local_settings(tmp_path))
    assert not isinstance(memory.get_cache(CacheScope.RATE_LIMIT), CacheBreakerImpl)
    assert not isinstance(memory.get_topics(), TopicsBreakerImpl)
    valkey = InfraConfiguredImpl(local_settings(tmp_path, **CLOUD_BACKENDS))
    assert all(isinstance(valkey.get_cache(scope), CacheBreakerImpl) for scope in CacheScope)
    assert isinstance(valkey.get_topics(), TopicsBreakerImpl)


def test_every_cache_scope_and_the_publisher_share_one_breaker(tmp_path: Path) -> None:
    """One breaker stands for one dependency, not for one interface: the four
    scopes and the publisher talk to one Valkey, so the first of them to pay
    the timeouts opens it for all of them."""
    infra = InfraConfiguredImpl(local_settings(tmp_path, **CLOUD_BACKENDS))
    breakers = {
        id(cast(CacheBreakerImpl, infra.get_cache(scope))._breaker)  # pyright: ignore[reportPrivateUsage]
        for scope in CacheScope
    }
    breakers.add(id(cast(TopicsBreakerImpl, infra.get_topics())._breaker))  # pyright: ignore[reportPrivateUsage]
    assert len(breakers) == 1


def test_a_valkey_topics_backend_alone_still_has_its_breaker(tmp_path: Path) -> None:
    """The breaker follows the connection, not the cache: a process on the
    memory cache and the Valkey bus still bounds what the bus costs it."""
    infra = InfraConfiguredImpl(local_settings(tmp_path, topics_backend="valkey"))
    assert isinstance(infra.get_topics(), TopicsBreakerImpl)
    assert not isinstance(infra.get_cache(CacheScope.RATE_LIMIT), CacheBreakerImpl)


def test_the_breaker_bounds_come_from_settings(tmp_path: Path) -> None:
    infra = InfraConfiguredImpl(
        local_settings(
            tmp_path,
            **CLOUD_BACKENDS,
            valkey_breaker_failures=7,
            valkey_breaker_cooldown_seconds=2.5,
        )
    )
    assert infra.get_cache(CacheScope.RATE_LIMIT).describe().endswith("+breaker(7/2.5s)")
    assert infra.get_topics().describe() == "topics=valkey+breaker(7/2.5s)"


@pytest.mark.parametrize(
    "field,value", [("valkey_breaker_failures", 0), ("valkey_breaker_cooldown_seconds", 0)]
)
def test_a_breaker_that_could_not_work_is_refused(tmp_path: Path, field: str, value: float) -> None:
    """A bound of zero failures opens on nothing and a cool-down of zero
    refuses nothing; neither is a breaker, so settings will not carry them."""
    with pytest.raises(ValidationError):
        local_settings(tmp_path, **{field: value})


def test_every_cache_scope_is_built_at_construction(tmp_path: Path) -> None:
    """No lazy member: every scope's cache exists before the first request
    and the boot line names it (ADR 0007)."""
    infra = InfraConfiguredImpl(local_settings(tmp_path))
    built = {scope: infra.get_cache(scope) for scope in CacheScope}
    assert all(infra.get_cache(scope) is cache for scope, cache in built.items())
    assert [line for line in infra.describe() if line.startswith("cache[")] == [
        cache.describe() for cache in built.values()
    ]


async def test_secret_overrides_reach_the_local_secrets_impl(tmp_path: Path) -> None:
    org = new_id()
    infra = InfraConfiguredImpl(
        local_settings(tmp_path, secret_overrides={f"{org.hex}_API_TOKEN".upper(): "from-boot"})
    )
    assert await infra.get_secrets().get(org, "api_token") == "from-boot"


@pytest.mark.parametrize("value", ["", "off", " OFF "])
def test_an_empty_or_off_sentry_dsn_turns_reporting_off(tmp_path: Path, value: str) -> None:
    assert local_settings(tmp_path, sentry_dsn=value).sentry_dsn is None


def test_a_sentry_dsn_is_kept(tmp_path: Path) -> None:
    dsn = "http://key@glitchtip:8000/1"
    assert local_settings(tmp_path, sentry_dsn=dsn).sentry_dsn == dsn
