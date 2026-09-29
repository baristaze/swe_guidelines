"""The infra root that picks impls from settings and refuses combinations
that are only safe locally."""

from datetime import timedelta

import aioboto3

from acme.infra.breaker import Breaker
from acme.infra.buckets import BucketsInterface
from acme.infra.buckets.local import BucketsLocalImpl
from acme.infra.buckets.s3 import BucketsS3Impl
from acme.infra.cache import CacheInterface, CacheScope
from acme.infra.cache.breaker import CacheBreakerImpl
from acme.infra.cache.memory import CacheMemoryImpl
from acme.infra.cache.valkey import CacheValkeyImpl
from acme.infra.exceptions import InfraException
from acme.infra.impl.settings import ENVIRONMENTS, InfraSettings
from acme.infra.impl.valkey import ValkeyConnection
from acme.infra.queues import QueuesInterface
from acme.infra.queues.memory import QueueMemoryImpl
from acme.infra.queues.sqs import QueueSqsImpl
from acme.infra.root import InfraInterface
from acme.infra.secrets import SecretsInterface
from acme.infra.secrets.aws import SecretsAwsImpl
from acme.infra.secrets.local import SecretsLocalImpl
from acme.infra.topics import TopicsInterface
from acme.infra.topics.breaker import TopicsBreakerImpl
from acme.infra.topics.memory import TopicsMemoryImpl
from acme.infra.topics.valkey import TopicsValkeyImpl


class UnsafeConfiguration(InfraException):
    code = "unsafe_configuration"


UNSAFE_IN_CLOUD: tuple[tuple[str, str, str], ...] = (
    ("secrets_backend", "local", "ACME_SECRETS_BACKEND"),
    ("cache_backend", "memory", "ACME_CACHE_BACKEND"),
    ("topics_backend", "memory", "ACME_TOPICS_BACKEND"),
    ("buckets_backend", "local", "ACME_BUCKETS_BACKEND"),
    ("queues_backend", "memory", "ACME_QUEUES_BACKEND"),
)


def refuse_unsafe(settings: InfraSettings) -> None:
    """Each refusal is a one-line check that exits naming the setting. An
    environment name outside the known set is refused first, so a deployed
    process cannot slip past the cloud checks under a misspelt name."""
    if not settings.is_known_environment:
        raise UnsafeConfiguration(
            f"ACME_ENVIRONMENT={settings.environment} is not one of "
            f"{', '.join(sorted(ENVIRONMENTS))}"
        )
    if not settings.is_cloud_environment:
        return
    for field, unsafe_value, env_name in UNSAFE_IN_CLOUD:
        if getattr(settings, field) == unsafe_value:
            raise UnsafeConfiguration(
                f"{env_name}={unsafe_value} is refused when ACME_ENVIRONMENT={settings.environment}"
            )


class InfraConfiguredImpl(InfraInterface):
    def __init__(self, settings: InfraSettings) -> None:
        refuse_unsafe(settings)
        self._settings = settings
        # One connection, and one breaker in front of it. A breaker stands for
        # a dependency, not for an interface, so every cache scope and the
        # topic publisher share this one: the first of them to pay the timeouts
        # opens it for all of them, instead of each paying its own bound over
        # again before it protects itself.
        self._valkey: ValkeyConnection | None = None
        self._valkey_breaker: Breaker | None = None
        if settings.cache_backend == "valkey" or settings.topics_backend == "valkey":
            self._valkey = ValkeyConnection(
                settings.valkey_url, timedelta(seconds=settings.valkey_timeout_seconds)
            )
            self._valkey_breaker = Breaker(
                "valkey_breaker",
                failures=settings.valkey_breaker_failures,
                cooldown=timedelta(seconds=settings.valkey_breaker_cooldown_seconds),
                slow=timedelta(seconds=settings.valkey_timeout_seconds),
            )
        aws_timeout = timedelta(seconds=settings.aws_timeout_seconds)
        self._aws = aioboto3.Session(
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.aws_region,
        )
        # One cache per scope, built now like every other member (ADR 0007),
        # so the boot line lists each one and get_cache is a lookup.
        self._caches: dict[CacheScope, CacheInterface] = {
            scope: self._build_cache(scope) for scope in CacheScope
        }

        if settings.buckets_backend == "s3":
            self._buckets: BucketsInterface = BucketsS3Impl(
                self._aws,
                endpoint_url=settings.s3_endpoint_url,
                region=settings.aws_region,
                bucket_prefix=settings.s3_bucket_prefix,
                timeout=aws_timeout,
                presign_endpoint_url=settings.s3_presign_endpoint_url,
            )
        else:
            self._buckets = BucketsLocalImpl(settings.buckets_root)

        if settings.topics_backend == "valkey":
            assert self._valkey is not None
            assert self._valkey_breaker is not None
            # The same breaker the caches hold: one Valkey, one dependency.
            self._topics: TopicsInterface = TopicsBreakerImpl(
                TopicsValkeyImpl(self._valkey), self._valkey_breaker
            )
        else:
            # In process, like the memory cache: it cannot time out.
            self._topics = TopicsMemoryImpl()

        if settings.queues_backend == "sqs":
            self._queues: QueuesInterface = QueueSqsImpl(
                self._aws,
                endpoint_url=settings.sqs_endpoint_url,
                region=settings.aws_region,
                queue_prefix=settings.sqs_queue_prefix,
                timeout=aws_timeout,
            )
        else:
            self._queues = QueueMemoryImpl()

        if settings.secrets_backend == "aws":
            self._secrets: SecretsInterface = SecretsAwsImpl(
                self._aws,
                region=settings.aws_region,
                name_prefix=settings.secrets_name_prefix,
                timeout=aws_timeout,
            )
        else:
            self._secrets = SecretsLocalImpl(settings.secrets_file, settings.secret_overrides)

    def _build_cache(self, scope: CacheScope) -> CacheInterface:
        """Only the out-of-process impl is wrapped. The memory impl is a dict
        on this event loop: it cannot time out and cannot be down, so a breaker
        in front of it would count nothing, refuse nothing, and cost every call
        a layer and every boot line a word that says nothing."""
        if self._settings.cache_backend == "valkey":
            assert self._valkey is not None
            assert self._valkey_breaker is not None
            return CacheBreakerImpl(CacheValkeyImpl(self._valkey, scope), self._valkey_breaker)
        return CacheMemoryImpl(scope)

    def get_cache(self, scope: CacheScope) -> CacheInterface:
        return self._caches[scope]

    def get_buckets(self) -> BucketsInterface:
        return self._buckets

    def get_topics(self) -> TopicsInterface:
        return self._topics

    def get_queues(self) -> QueuesInterface:
        return self._queues

    def get_secrets(self) -> SecretsInterface:
        return self._secrets

    def describe(self) -> list[str]:
        return [
            *(cache.describe() for cache in self._caches.values()),
            self._topics.describe(),
            self._buckets.describe(),
            self._queues.describe(),
            self._secrets.describe(),
        ]

    async def start(self) -> None:
        if self._valkey is not None:
            await self._valkey.start()
        for capability in (self._topics, self._buckets, self._queues, self._secrets):
            await capability.start()

    async def close(self) -> None:
        """Reverse order of start; the caches first and the shared client
        last, once nothing holds it."""
        for cache in self._caches.values():
            await cache.close()
        for capability in (self._secrets, self._queues, self._buckets, self._topics):
            await capability.close()
        if self._valkey is not None:
            await self._valkey.close()
