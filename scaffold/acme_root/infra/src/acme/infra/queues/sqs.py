import json
from datetime import datetime, timedelta
from typing import Any

import aioboto3

from acme.infra.aws_clients import AwsClientHolder, client_config
from acme.infra.aws_errors import translated
from acme.infra.deadline import bounded, unreachable
from acme.infra.observability import OUTCOMES
from acme.infra.queues import QueueDepth, QueueMessage, Queues, QueuesInterface

LONG_POLL_MAX = timedelta(seconds=20)
"""The hosted queue's own ceiling on a receive's wait."""

LONG_POLL_MARGIN = timedelta(seconds=2)
"""A long poll ends this far before the client's read timeout. An empty queue
answers at the wait's end plus a round trip; a wait at or past the read timeout
turns every empty poll into a read timeout instead of an empty answer."""


class QueueSqsImpl(QueuesInterface):
    """The hosted queue moves a message to its dead-letter queue itself, after
    the redrive policy's receive count, so that transition is not observable
    here; `depth()` reports the dead-lettered count. A receive's wait is capped
    below the client's read timeout, so the timeout from settings bounds every
    attempt, a long poll included."""

    def __init__(
        self,
        session: aioboto3.Session,
        *,
        endpoint_url: str | None,
        region: str,
        queue_prefix: str,
        timeout: timedelta,
    ) -> None:
        self._endpoint_url = endpoint_url
        self._region = region
        config = client_config(timeout)
        self._holder = AwsClientHolder(
            "sqs",
            lambda: session.client(
                "sqs", endpoint_url=endpoint_url, region_name=region, config=config
            ),
        )
        self._queue_prefix = queue_prefix
        self._max_wait = max(timedelta(0), min(LONG_POLL_MAX, timeout - LONG_POLL_MARGIN))
        self._urls: dict[str, str] = {}

    def _client(self) -> Any:
        return self._holder.client()

    async def _url(self, sqs: Any, name: str) -> str:
        if name not in self._urls:
            response = await sqs.get_queue_url(QueueName=name)
            self._urls[name] = response["QueueUrl"]
        return self._urls[name]

    def _name(self, queue: Queues) -> str:
        return f"{self._queue_prefix}{queue.value}"

    async def send(self, queue: Queues, body: bytes, *, deadline: datetime | None = None) -> None:
        async with bounded(deadline, unreachable("sqs", "send")):
            with translated("sqs", "send"):
                sqs = self._client()
                await sqs.send_message(
                    QueueUrl=await self._url(sqs, self._name(queue)),
                    MessageBody=body.decode(),
                )
        OUTCOMES.labels(subsystem="queue", outcome="sent").inc()

    async def receive(
        self, queue: Queues, max_messages: int, wait: timedelta, visibility: timedelta
    ) -> list[QueueMessage]:
        with translated("sqs", "receive"):
            sqs = self._client()
            response = await sqs.receive_message(
                QueueUrl=await self._url(sqs, self._name(queue)),
                MaxNumberOfMessages=max(1, min(max_messages, 10)),
                WaitTimeSeconds=int(min(wait, self._max_wait).total_seconds()),
                VisibilityTimeout=int(visibility.total_seconds()),
                AttributeNames=["ApproximateReceiveCount"],
            )
        received = [
            QueueMessage(
                id=m["MessageId"],
                body=m["Body"].encode(),
                receipt=m["ReceiptHandle"],
                attempts=int(m.get("Attributes", {}).get("ApproximateReceiveCount", "1")),
            )
            for m in response.get("Messages", [])
        ]
        OUTCOMES.labels(subsystem="queue", outcome="received").inc(len(received))
        return received

    async def delete(self, queue: Queues, receipt: str) -> None:
        with translated("sqs", "delete"):
            sqs = self._client()
            await sqs.delete_message(
                QueueUrl=await self._url(sqs, self._name(queue)), ReceiptHandle=receipt
            )
        OUTCOMES.labels(subsystem="queue", outcome="deleted").inc()

    async def change_visibility(self, queue: Queues, receipt: str, visibility: timedelta) -> None:
        with translated("sqs", "change_visibility"):
            sqs = self._client()
            await sqs.change_message_visibility(
                QueueUrl=await self._url(sqs, self._name(queue)),
                ReceiptHandle=receipt,
                VisibilityTimeout=int(visibility.total_seconds()),
            )

    async def depth(self, queue: Queues) -> QueueDepth:
        with translated("sqs", "depth"):
            sqs = self._client()
            url = await self._url(sqs, self._name(queue))
            response = await sqs.get_queue_attributes(
                QueueUrl=url,
                AttributeNames=[
                    "ApproximateNumberOfMessages",
                    "ApproximateNumberOfMessagesNotVisible",
                    "RedrivePolicy",
                ],
            )
            attributes = response.get("Attributes", {})
            dead = 0
            redrive = attributes.get("RedrivePolicy")
            if redrive:
                dead_name = json.loads(redrive)["deadLetterTargetArn"].rsplit(":", 1)[-1]
                dead_response = await sqs.get_queue_attributes(
                    QueueUrl=await self._url(sqs, dead_name),
                    AttributeNames=["ApproximateNumberOfMessages"],
                )
                dead = int(dead_response["Attributes"]["ApproximateNumberOfMessages"])
        return QueueDepth(
            visible=int(attributes.get("ApproximateNumberOfMessages", "0")),
            in_flight=int(attributes.get("ApproximateNumberOfMessagesNotVisible", "0")),
            dead_lettered=dead,
        )

    def describe(self) -> str:
        return f"queues=sqs({self._endpoint_url or self._region})"

    async def start(self) -> None:
        await self._holder.open()

    async def close(self) -> None:
        await self._holder.close()
