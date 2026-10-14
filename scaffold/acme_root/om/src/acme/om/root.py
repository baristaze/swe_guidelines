"""The business-layer root: constructs every manager in dependency order and
hands back one frozen object with a field per manager."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from acme.infra.cache import CacheInterface, CacheScope
from acme.infra.root import InfraInterface
from acme.integrations.identity import IdentityProviderInterface
from acme.integrations.identity.absent import IdentityProviderAbsentImpl
from acme.integrations.root import IntegrationsInterface
from acme.om.base import utcnow
from acme.om.change_events import ChangeEventsManagerInterface
from acme.om.change_events.impl.manager import ChangeEventsManagerImpl, ChangeEventsOptions
from acme.om.idempotency import IdempotencyManagerInterface
from acme.om.idempotency.impl.manager import IdempotencyManagerImpl, IdempotencyOptions
from acme.om.leases import LeasesManagerInterface
from acme.om.leases.impl.kinds import NoopLeasableResourceKindImpl, OrchestrationWaiterImpl
from acme.om.leases.impl.manager import LeasesManagerImpl, LeasesOptions
from acme.om.leases.types.leasable_resource import LeasableResourceKind
from acme.om.leases.types.request import WaiterKind
from acme.om.media import MediaManagerInterface
from acme.om.media.impl.manager import MediaManagerImpl, MediaOptions
from acme.om.orchestrations import OrchestrationsManagerInterface
from acme.om.orchestrations.impl.manager import OrchestrationsManagerImpl, OrchestrationsOptions
from acme.om.orchestrations.kinds import NOOP_ORCHESTRATION, OrchestrationKinds
from acme.om.outbox import OutboxRelayInterface
from acme.om.outbox.impl.relay import OutboxRelayImpl
from acme.om.storage.root import StorageInterface
from acme.om.tenancy import TenancyManagerInterface, TenancyOperatorManagerInterface
from acme.om.tenancy.impl.credentials import TenancyCredentialsManagerImpl
from acme.om.tenancy.impl.manager import TenancyManagerImpl, TenancyOptions
from acme.om.tenancy.impl.members import TenancyMembersManagerImpl
from acme.om.tenancy.impl.operator import TenancyOperatorManagerImpl, TenancyOperatorOptions
from acme.om.tenancy.impl.org import TenancyOrgManagerImpl
from acme.om.tenancy.impl.sign_in import TenancySignInManagerImpl
from acme.om.tenancy.storage import TenancyStorageInterface
from acme.om.work import WorkManagerInterface, WorkOperatorManagerInterface
from acme.om.work.impl.manager import WorkManagerImpl, WorkOptions
from acme.om.work.impl.operator import WorkOperatorManagerImpl
from acme.om.work.kinds import WORK_KINDS, WorkKinds

KINDS = WorkKinds(
    (
        *WORK_KINDS,
        # A copy adds its entries below this line.
    )
)
"""Every kind of work this process knows: the work namespace's own, then
each namespace's that declares kinds in its own `kinds.py`. The work
manager reads every kind through it, and the worker holds its handlers to
it."""

ORCHESTRATION_KINDS = OrchestrationKinds(
    (
        NOOP_ORCHESTRATION,
        # A copy adds its entries below this line.
    )
)
"""Every kind of long-running record this process knows: the mechanism's
own, then each namespace's that declares kinds in its own `kinds.py`. The
orchestrations manager holds every start to it, and the worker holds its
steps to it."""


@dataclass(frozen=True)
class Managers:
    change_events: ChangeEventsManagerInterface
    idempotency: IdempotencyManagerInterface
    leases: LeasesManagerInterface
    media: MediaManagerInterface
    orchestrations: OrchestrationsManagerInterface
    outbox: OutboxRelayInterface
    tenancy: TenancyManagerInterface
    tenancy_operator: TenancyOperatorManagerInterface
    work: WorkManagerInterface
    work_operator: WorkOperatorManagerInterface
    # A copy adds its entries below this line.


def build_managers(
    storage: StorageInterface,
    infra: InfraInterface,
    tenancy_options: TenancyOptions | None = None,
    operator_options: TenancyOperatorOptions | None = None,
    integrations: IntegrationsInterface | None = None,
    *,
    change_events_options: ChangeEventsOptions | None = None,
    idempotency_options: IdempotencyOptions | None = None,
    leases_options: LeasesOptions | None = None,
    media_options: MediaOptions | None = None,
    orchestration_kinds: OrchestrationKinds = ORCHESTRATION_KINDS,
    orchestrations_options: OrchestrationsOptions | None = None,
    work_kinds: WorkKinds = KINDS,
    work_options: WorkOptions | None = None,
    # A copy adds its entries below this line.
) -> Managers:
    """`integrations` is the root of the hosted services the managers front:
    the identity provider, which the tenancy manager signs people in and
    invites them through. None is a process that signs nobody in, and every
    call that would reach the provider is refused as unavailable.

    The options after `integrations` are what the process that sweeps sets
    on the managers it purges through: each one's retention and batch. None
    keeps that manager's defaults. `work_kinds` and `orchestration_kinds`
    are the registries the work and the orchestrations managers read every
    kind through: `KINDS` and `ORCHESTRATION_KINDS`, unless a test hands
    another.

    Each manager is one call to its builder below, in dependency order: a
    builder takes the storage, the infrastructure, and the sibling managers
    it needs, by their interfaces, and nothing else."""
    # The relay every core-role manager hands its outbox rows to. It reaches
    # the work manager through the root below, because a row of kind
    # `work.<kind>` is enqueued there: the work manager needs the tenancy
    # manager, which needs this relay, so that one edge is bound at call time
    # and the graph the root hands back is still whole.
    outbox = build_outbox(storage, infra, lambda: managers.work)
    tenancy = build_tenancy(
        storage.get_tenancy_storage(),
        outbox,
        infra.get_cache(CacheScope.REALTIME_TICKET),
        tenancy_options or TenancyOptions(),
        identity_provider=(
            IdentityProviderAbsentImpl()
            if integrations is None
            else integrations.get_identity_provider()
        ),
    )
    tenancy_operator = build_tenancy_operator(
        storage, outbox, operator_options or TenancyOperatorOptions()
    )
    change_events = build_change_events(
        storage, tenancy, change_events_options or ChangeEventsOptions()
    )
    work = build_work(
        storage, infra, tenancy, change_events, work_options or WorkOptions(), work_kinds
    )
    work_operator = build_work_operator(storage, infra)
    media = build_media(storage, infra, tenancy, outbox, media_options or MediaOptions())
    orchestrations = build_orchestrations(
        storage,
        tenancy,
        outbox,
        orchestrations_options or OrchestrationsOptions(),
        orchestration_kinds,
    )
    leases = build_leases(
        storage, tenancy, outbox, work, orchestrations, leases_options or LeasesOptions()
    )
    idempotency = build_idempotency(storage, idempotency_options or IdempotencyOptions())
    # A copy adds its entries below this line.
    managers = Managers(
        change_events=change_events,
        idempotency=idempotency,
        leases=leases,
        media=media,
        orchestrations=orchestrations,
        outbox=outbox,
        tenancy=tenancy,
        tenancy_operator=tenancy_operator,
        work=work,
        work_operator=work_operator,
        # A copy adds its entries below this line.
    )
    return managers


def build_change_events(
    storage: StorageInterface, tenancy: TenancyManagerInterface, options: ChangeEventsOptions
) -> ChangeEventsManagerInterface:
    return ChangeEventsManagerImpl(storage.get_change_event_storage(), tenancy, options)


def build_idempotency(
    storage: StorageInterface, options: IdempotencyOptions
) -> IdempotencyManagerInterface:
    return IdempotencyManagerImpl(storage.get_idempotency_storage(), options)


def build_leases(
    storage: StorageInterface,
    tenancy: TenancyManagerInterface,
    outbox: OutboxRelayInterface,
    work: WorkManagerInterface,
    orchestrations: OrchestrationsManagerInterface,
    options: LeasesOptions,
) -> LeasesManagerInterface:
    """A copy registers its resource kinds and its waiter kinds here, below
    the marker, each by the name its namespace declares with its hooks, as a
    work kind's handler is registered in the worker, and, for a kind that
    refuses some asks, its check (`asks`)."""
    return LeasesManagerImpl(
        storage.get_lease_storage(),
        tenancy,
        outbox,
        options,
        kinds={
            LeasableResourceKind.NOOP: NoopLeasableResourceKindImpl(),
            # A copy adds its entries below this line.
        },
        waiters={
            WaiterKind.ORCHESTRATION: OrchestrationWaiterImpl(orchestrations),
            # A copy adds its entries below this line.
        },
        work=work,
    )


def build_media(
    storage: StorageInterface,
    infra: InfraInterface,
    tenancy: TenancyManagerInterface,
    outbox: OutboxRelayInterface,
    options: MediaOptions,
) -> MediaManagerInterface:
    return MediaManagerImpl(
        storage.get_media_storage(),
        infra.get_buckets(),
        tenancy,
        outbox,
        infra.get_flags(),
        options,
    )


def build_orchestrations(
    storage: StorageInterface,
    tenancy: TenancyManagerInterface,
    outbox: OutboxRelayInterface,
    options: OrchestrationsOptions,
    kinds: OrchestrationKinds,
) -> OrchestrationsManagerInterface:
    return OrchestrationsManagerImpl(
        storage.get_orchestrations_storage(), tenancy, outbox, options, kinds
    )


def build_outbox(
    storage: StorageInterface,
    infra: InfraInterface,
    work: Callable[[], WorkManagerInterface],
) -> OutboxRelayInterface:
    """`work` is called when a row asks for work, never here: the work
    manager is built after the relay it is reached through."""
    return OutboxRelayImpl(
        storage.get_outbox_storage(),
        storage.get_change_event_storage(),
        infra.get_topics(),
        work,
    )


def build_tenancy(
    storage: TenancyStorageInterface,
    relay: OutboxRelayInterface,
    cache: CacheInterface,
    options: TenancyOptions,
    clock: Callable[[], datetime] = utcnow,
    *,
    identity_provider: IdentityProviderInterface,
) -> TenancyManagerInterface:
    """The tenancy manager with its delegates, each built here and handed to
    it: a caller outside the namespace reaches a delegate through the
    manager, and no impl builds another. A delegate that calls a sibling
    takes it here, by its interface, and one that needs an operation of the
    manager takes that one operation as a callable. `clock` is the one the
    second factor's time step is read from."""
    sign_in = TenancySignInManagerImpl(
        storage, relay, options, clock, identity_provider=identity_provider
    )
    # The account's deletion writes a row under each place its person holds,
    # and a stage comes only from the manager's transition. The manager holds
    # this delegate, so that one edge is bound at call time.
    org = TenancyOrgManagerImpl(
        storage,
        relay,
        options,
        identity_provider=identity_provider,
        service_context=lambda rctx, org_id, user_id: tenancy.service_context(
            rctx, org_id, user_id
        ),
    )
    members = TenancyMembersManagerImpl(
        storage, relay, options, org=org, identity_provider=identity_provider
    )
    credentials = TenancyCredentialsManagerImpl(storage, relay, options)
    tenancy = TenancyManagerImpl(
        storage,
        relay,
        cache,
        options,
        sign_in=sign_in,
        org=org,
        members=members,
        credentials=credentials,
    )
    return tenancy


def build_tenancy_operator(
    storage: StorageInterface, outbox: OutboxRelayInterface, options: TenancyOperatorOptions
) -> TenancyOperatorManagerInterface:
    return TenancyOperatorManagerImpl(
        storage.get_tenancy_storage(), storage.get_change_event_storage(), outbox, options
    )


def build_work(
    storage: StorageInterface,
    infra: InfraInterface,
    tenancy: TenancyManagerInterface,
    change_events: ChangeEventsManagerInterface,
    options: WorkOptions,
    kinds: WorkKinds,
) -> WorkManagerInterface:
    return WorkManagerImpl(
        storage.get_work_storage(), tenancy, change_events, infra.get_topics(), options, kinds
    )


def build_work_operator(
    storage: StorageInterface, infra: InfraInterface
) -> WorkOperatorManagerInterface:
    return WorkOperatorManagerImpl(
        storage.get_work_storage(),
        storage.get_tenancy_storage(),
        storage.get_change_event_storage(),
        infra.get_topics(),
    )


# A copy adds its entries below this line.
