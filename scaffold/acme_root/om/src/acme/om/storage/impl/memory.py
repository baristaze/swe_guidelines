"""The in-memory storage root: the default for unit tests and the fast gate."""

from acme.om.billing.storage import BillingStorageInterface
from acme.om.billing.storage.impl.memory import BillingStorageMemoryImpl
from acme.om.events.storage import EventStorageInterface
from acme.om.events.storage.impl.memory import EventStorageMemoryImpl
from acme.om.idempotency.storage import IdempotencyStorageInterface
from acme.om.idempotency.storage.impl.memory import IdempotencyStorageMemoryImpl
from acme.om.media.storage import MediaStorageInterface
from acme.om.media.storage.impl.memory import MediaStorageMemoryImpl
from acme.om.orchestrations.storage import OrchestrationsStorageInterface
from acme.om.orchestrations.storage.impl.memory import OrchestrationsStorageMemoryImpl
from acme.om.outbox.storage import OutboxStorageInterface
from acme.om.outbox.storage.impl.memory import OutboxStorageMemoryImpl
from acme.om.slack.storage import SlackStorageInterface
from acme.om.slack.storage.impl.memory import SlackStorageMemoryImpl
from acme.om.storage.root import StorageInterface
from acme.om.tasks.storage import TasksStorageInterface
from acme.om.tasks.storage.impl.memory import TasksStorageMemoryImpl
from acme.om.tenancy.storage import TenancyStorageInterface
from acme.om.tenancy.storage.impl.memory import TenancyStorageMemoryImpl
from acme.om.work.storage import WorkStorageInterface
from acme.om.work.storage.impl.memory import WorkStorageMemoryImpl


class StorageMemoryImpl(StorageInterface):
    def __init__(self) -> None:
        # The outbox and the markers first: the core-role impls land their
        # outbox rows in the one and fence a re-mint on the other, which is
        # how each impl gets what its Postgres twin reads in its own statement.
        self._outbox = OutboxStorageMemoryImpl()
        self._idempotency = IdempotencyStorageMemoryImpl()
        # The billing accounts before tenancy: an api key's principal is read
        # with its org's account, which the Postgres impl joins.
        self._billing = BillingStorageMemoryImpl(self._outbox)
        self._tenancy = TenancyStorageMemoryImpl(self._outbox, self._idempotency, self._billing)
        self._work = WorkStorageMemoryImpl()
        # The records first: a step of one lands beside the tasks it changes.
        self._orchestrations = OrchestrationsStorageMemoryImpl(self._outbox)
        self._tasks = TasksStorageMemoryImpl(self._outbox, self._orchestrations)
        self._media = MediaStorageMemoryImpl(self._outbox)
        self._events = EventStorageMemoryImpl()
        self._slack = SlackStorageMemoryImpl(self._outbox)

    def get_tenancy_storage(self) -> TenancyStorageInterface:
        return self._tenancy

    def get_work_storage(self) -> WorkStorageInterface:
        return self._work

    def get_tasks_storage(self) -> TasksStorageInterface:
        return self._tasks

    def get_media_storage(self) -> MediaStorageInterface:
        return self._media

    def get_idempotency_storage(self) -> IdempotencyStorageInterface:
        return self._idempotency

    def get_event_storage(self) -> EventStorageInterface:
        return self._events

    def get_outbox_storage(self) -> OutboxStorageInterface:
        return self._outbox

    def get_billing_storage(self) -> BillingStorageInterface:
        return self._billing

    def get_slack_storage(self) -> SlackStorageInterface:
        return self._slack

    def get_orchestrations_storage(self) -> OrchestrationsStorageInterface:
        return self._orchestrations

    async def healthcheck(self) -> bool:
        return True

    async def close(self) -> None:
        return None
