"""The in-process half every topic impl shares: local subscribers and the
dispatch to them. Each handler failure is logged and never stops the rest."""

import itertools
import logging
from collections.abc import Callable

from acme.infra.exceptions import PayloadMismatch
from acme.infra.topics import TOPIC_PAYLOADS, TopicHandler, TopicPayload, Topics

log = logging.getLogger(__name__)


class LocalSubscribers:
    def __init__(self) -> None:
        self._ids = itertools.count()
        self._handlers: dict[Topics, dict[int, tuple[str, TopicHandler]]] = {}

    def add(self, topic: Topics, consumer: str, handler: TopicHandler) -> Callable[[], None]:
        handler_id = next(self._ids)
        self._handlers.setdefault(topic, {})[handler_id] = (consumer, handler)

        def unsubscribe() -> None:
            self._handlers.get(topic, {}).pop(handler_id, None)

        return unsubscribe

    async def dispatch(self, topic: Topics, payload: TopicPayload) -> None:
        for consumer, handler in list(self._handlers.get(topic, {}).values()):
            try:
                await handler(payload)
            except Exception:
                log.exception(
                    "topic %s consumer %s failed on %s",
                    topic.value,
                    consumer,
                    payload.idempotency_key,
                )


def check_payload(topic: Topics, payload: TopicPayload) -> None:
    expected = TOPIC_PAYLOADS[topic]
    if not isinstance(payload, expected):
        raise PayloadMismatch(
            f"{topic.value} carries {expected.__name__}, not {type(payload).__name__}"
        )
