from collections.abc import Callable

from acme.infra.topics import TopicHandler, TopicPayload, Topics, TopicsInterface
from acme.infra.topics.dispatch import LocalSubscribers, check_payload


class TopicsMemoryImpl(TopicsInterface):
    """An in-process dispatcher: at-least-once to every handler subscribed now."""

    def __init__(self) -> None:
        self._subscribers = LocalSubscribers()

    async def publish(self, topic: Topics, payload: TopicPayload) -> bool:
        check_payload(topic, payload)
        await self._subscribers.dispatch(topic, payload)
        return True

    def subscribe(self, topic: Topics, consumer: str, handler: TopicHandler) -> Callable[[], None]:
        return self._subscribers.add(topic, consumer, handler)

    def describe(self) -> str:
        return "topics=memory"

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None
