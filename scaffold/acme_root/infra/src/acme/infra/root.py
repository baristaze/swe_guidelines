"""Infrastructure is fronted by a single root so consumers can ask for what
they need. The root has a lifecycle because some capabilities do, and every
capability declares one so the root never asks which."""

from abc import ABC, abstractmethod

from acme.infra.buckets import BucketsInterface
from acme.infra.cache import CacheInterface, CacheScope
from acme.infra.flags import FlagsInterface
from acme.infra.queues import QueuesInterface
from acme.infra.secrets import SecretsInterface
from acme.infra.topics import TopicsInterface


class InfraInterface(ABC):
    @abstractmethod
    def get_cache(self, scope: CacheScope) -> CacheInterface: ...

    @abstractmethod
    def get_buckets(self) -> BucketsInterface: ...

    @abstractmethod
    def get_topics(self) -> TopicsInterface: ...

    @abstractmethod
    def get_queues(self) -> QueuesInterface: ...

    @abstractmethod
    def get_secrets(self) -> SecretsInterface: ...

    @abstractmethod
    def get_flags(self) -> FlagsInterface: ...

    @abstractmethod
    def describe(self) -> list[str]:
        """One line per chosen backend, logged once at boot."""
        ...

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...
