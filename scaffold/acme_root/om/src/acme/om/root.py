"""The business-layer root: constructs every manager in dependency order and
hands back one frozen object with a field per manager."""

from dataclasses import dataclass

from acme.infra.cache import CacheScope
from acme.infra.root import InfraInterface
from acme.integrations.identity.absent import IdentityProviderAbsentImpl
from acme.integrations.impl.configured import absent_payments
from acme.integrations.root import IntegrationsInterface
from acme.integrations.slack.off import SlackOffImpl
from acme.om.billing import BillingManagerInterface, BillingOperatorManagerInterface
from acme.om.billing.impl.manager import BillingManagerImpl, BillingOptions
from acme.om.billing.impl.operator import BillingOperatorManagerImpl
from acme.om.events import EventsManagerInterface
from acme.om.events.impl.manager import EventsManagerImpl, EventsOptions
from acme.om.idempotency import IdempotencyManagerInterface
from acme.om.idempotency.impl.manager import IdempotencyManagerImpl, IdempotencyOptions
from acme.om.media import MediaManagerInterface
from acme.om.media.impl.manager import MediaManagerImpl, MediaOptions
from acme.om.orchestrations import OrchestrationsManagerInterface
from acme.om.orchestrations.impl.manager import OrchestrationsManagerImpl, OrchestrationsOptions
from acme.om.outbox import OutboxRelayInterface
from acme.om.outbox.impl.relay import OutboxRelayImpl
from acme.om.slack import SlackManagerInterface
from acme.om.slack.impl.manager import SlackManagerImpl, SlackOptions
from acme.om.storage.root import StorageInterface
from acme.om.tasks import TasksManagerInterface
from acme.om.tasks.impl.manager import TasksManagerImpl, TasksOptions
from acme.om.tenancy import TenancyManagerInterface, TenancyOperatorManagerInterface
from acme.om.tenancy.impl.manager import TenancyManagerImpl, TenancyOptions
from acme.om.tenancy.impl.operator import TenancyOperatorManagerImpl, TenancyOperatorOptions
from acme.om.work import WorkManagerInterface, WorkOperatorManagerInterface
from acme.om.work.impl.manager import WorkManagerImpl, WorkOptions
from acme.om.work.impl.operator import WorkOperatorManagerImpl


@dataclass(frozen=True)
class Managers:
    tenancy: TenancyManagerInterface
    tenancy_operator: TenancyOperatorManagerInterface
    work: WorkManagerInterface
    work_operator: WorkOperatorManagerInterface
    tasks: TasksManagerInterface
    media: MediaManagerInterface
    slack: SlackManagerInterface
    idempotency: IdempotencyManagerInterface
    events: EventsManagerInterface
    outbox: OutboxRelayInterface
    billing: BillingManagerInterface
    billing_operator: BillingOperatorManagerInterface
    orchestrations: OrchestrationsManagerInterface


def build_managers(
    storage: StorageInterface,
    infra: InfraInterface,
    tenancy_options: TenancyOptions | None = None,
    operator_options: TenancyOperatorOptions | None = None,
    integrations: IntegrationsInterface | None = None,
    *,
    tasks_options: TasksOptions | None = None,
    media_options: MediaOptions | None = None,
    idempotency_options: IdempotencyOptions | None = None,
    events_options: EventsOptions | None = None,
    billing_options: BillingOptions | None = None,
    slack_options: SlackOptions | None = None,
    work_options: WorkOptions | None = None,
    orchestrations_options: OrchestrationsOptions | None = None,
) -> Managers:
    """`integrations` is the root of the hosted services the managers front:
    the identity provider, which the tenancy manager signs people in and
    invites them through, the payment processor, which the billing manager
    mirrors, and the Slack app, which the slack manager installs and posts
    through. None is a process that signs nobody in and holds none of them,
    and every call that would reach one is refused as unavailable.

    The options after `integrations` are what the process that sweeps sets
    on the managers it purges through: each one's retention and batch, and
    the tasks manager's age at which the daily cleanup archives a done
    task. None keeps that manager's defaults."""
    # The relay every core-role manager hands its outbox rows to. It reaches
    # the work manager through the root below, because a row of kind
    # `work.<kind>` is enqueued there: the work manager needs the tenancy
    # manager, which needs this relay, so that one edge is bound at call time
    # and the graph the root hands back is still whole.
    outbox = OutboxRelayImpl(
        storage.get_outbox_storage(),
        storage.get_event_storage(),
        infra.get_topics(),
        lambda: managers.work,
    )
    # Billing and tenancy ask each other one question each: tenancy asks an
    # org's entitlements, and billing's sweep asks whether a tenant is past
    # its retention. That edge is bound at call time, as the relay's is.
    billing = BillingManagerImpl(
        storage.get_billing_storage(),
        absent_payments() if integrations is None else integrations.get_payments(),
        outbox,
        lambda: managers.tenancy,
        infra.get_cache(CacheScope.BILLING_ACCOUNT),
        billing_options or BillingOptions(),
    )
    tenancy = TenancyManagerImpl(
        storage.get_tenancy_storage(),
        outbox,
        infra.get_cache(CacheScope.REALTIME_TICKET),
        tenancy_options or TenancyOptions(),
        entitlements=billing,
        identity_provider=(
            IdentityProviderAbsentImpl()
            if integrations is None
            else integrations.get_identity_provider()
        ),
    )
    events = EventsManagerImpl(
        storage.get_event_storage(), tenancy, events_options or EventsOptions()
    )
    work = WorkManagerImpl(
        storage.get_work_storage(),
        tenancy,
        events,
        infra.get_topics(),
        work_options or WorkOptions(),
    )
    media = MediaManagerImpl(
        storage.get_media_storage(),
        infra.get_buckets(),
        tenancy,
        outbox,
        media_options or MediaOptions(),
    )
    slack = SlackManagerImpl(
        storage.get_slack_storage(),
        tenancy,
        outbox,
        SlackOffImpl() if integrations is None else integrations.get_slack(),
        infra.get_secrets(),
        slack_options or SlackOptions(),
    )
    orchestrations = OrchestrationsManagerImpl(
        storage.get_orchestrations_storage(),
        tenancy,
        outbox,
        orchestrations_options or OrchestrationsOptions(),
    )
    tasks = TasksManagerImpl(
        storage.get_tasks_storage(),
        tenancy,
        media,
        outbox,
        slack,
        tasks_options or TasksOptions(),
        entitlements=billing,
        orchestrations=orchestrations,
    )
    idempotency = IdempotencyManagerImpl(
        storage.get_idempotency_storage(), idempotency_options or IdempotencyOptions()
    )
    tenancy_operator = TenancyOperatorManagerImpl(
        storage.get_tenancy_storage(),
        storage.get_tasks_storage(),
        storage.get_event_storage(),
        outbox,
        operator_options or TenancyOperatorOptions(),
        billing=storage.get_billing_storage(),
    )
    managers = Managers(
        tenancy=tenancy,
        tenancy_operator=tenancy_operator,
        work=work,
        work_operator=WorkOperatorManagerImpl(
            storage.get_work_storage(),
            storage.get_tenancy_storage(),
            storage.get_event_storage(),
            infra.get_topics(),
        ),
        tasks=tasks,
        media=media,
        slack=slack,
        idempotency=idempotency,
        events=events,
        outbox=outbox,
        billing=billing,
        billing_operator=BillingOperatorManagerImpl(
            storage.get_billing_storage(),
            storage.get_tenancy_storage(),
            outbox,
            infra.get_cache(CacheScope.BILLING_ACCOUNT),
        ),
        orchestrations=orchestrations,
    )
    return managers
