"""The worker binary: settings, container, stop handlers, `serve`, and the
`health` probe, which asks the serving process's `/healthz`."""

import argparse
import asyncio
import logging
import signal
import sys
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable
from datetime import timedelta

from acme.infra.cache import CacheScope
from acme.infra.observability import (
    configure_error_reporting,
    configure_logging,
    configure_tracing,
    name_process,
)
from acme.infra.trust import install_trust_store
from acme.om.context import RequestContext
from acme.om.orchestrations.types.orchestration import OrchestrationKind
from acme.om.work.types.work_item import WorkKind
from acme.workers.maintenance.accounts import DeleteAccountHandlerImpl, DeleteOrgHandlerImpl
from acme.workers.maintenance.container import (
    LEASE_SWEEP_BATCH,
    MEDIA_PURGE_BATCH,
    WorkerContainer,
)
from acme.workers.maintenance.deliveries import (
    DeliveryConsumer,
    DeliveryOptions,
    IdentityDeliveriesImpl,
)
from acme.workers.maintenance.handler import NoopHandlerImpl
from acme.workers.maintenance.health import Probe, WorkerHttpServer
from acme.workers.maintenance.loop import AcrossStep, LoopOptions, WorkerLoop
from acme.workers.maintenance.orchestrations import (
    OrchestrationHandlerImpl,
    WakeParkedHandlerImpl,
)
from acme.workers.maintenance.providers import ProviderCalls
from acme.workers.maintenance.settings import MaintenanceSettings

log = logging.getLogger(__name__)

IDENTITY = "identity"
"""The identity provider, by the name its deliveries are queued under."""

IDENTITY_CREDENTIAL = "ACME_WORKOS_API_KEY"
"""The secret the worker's calls to it are made with: the platform's own, by
its name."""


def loop_options(settings: MaintenanceSettings, lane: str | None = None) -> LoopOptions:
    return LoopOptions(
        worker_id=settings.worker_id,
        lane=lane or settings.worker_lane,
        tenant_cap=settings.worker_tenant_cap or None,
        capacity=settings.worker_capacity,
        lease=timedelta(seconds=settings.worker_lease_seconds),
        heartbeat_interval=timedelta(seconds=settings.worker_heartbeat_seconds),
        sweep_interval=timedelta(seconds=settings.worker_sweep_seconds),
        poll_interval=timedelta(seconds=settings.worker_poll_seconds),
        outbox_retention=timedelta(days=settings.outbox_retention_days),
        purge_batch=settings.worker_purge_batch,
        sweep_budget=timedelta(seconds=settings.worker_sweep_budget_seconds),
        tally_interval=timedelta(seconds=settings.worker_tally_seconds),
    )


def unstaged(purge: Callable[[], Awaitable[int]]) -> AcrossStep:
    """A purge across tenants that takes no stage, since it runs for no tenant
    and no principal, as the loop calls it: with the pass's request stage."""

    async def step(rctx: RequestContext) -> int:
        return await purge()

    return step


def build_loop(container: WorkerContainer, lane: str | None = None) -> WorkerLoop:
    managers = container.managers
    # Every call to the identity provider reads, marks, and clears its outage
    # on the signal every worker shares.
    identity_calls = ProviderCalls(container.infra.get_outages(), IDENTITY, IDENTITY_CREDENTIAL)
    return WorkerLoop(
        work=managers.work,
        outbox=managers.outbox,
        # Per tenant, what only a tenant deleted past its retention has: every
        # row of it goes. Any other tenant costs these nothing.
        purges={
            # Every file's object, then its row.
            "media": managers.media.purge_tenant,
            "tenancy": managers.tenancy.purge_tenant,
            "events": managers.events.purge_tenant,
            "orchestrations": managers.orchestrations.purge_tenant,
            "leases": managers.leases.purge_tenant,
        },
        # Once a pass, across every tenant: each namespace's rows past their
        # retention.
        across={
            # A deleted file's object, then its row; an abandoned upload's too.
            "media": unstaged(managers.media.purge_across_tenants),
            "tenancy": unstaged(managers.tenancy.purge_across_tenants),
            "idempotency": unstaged(managers.idempotency.purge_across_tenants),
            # The trim: each tenant's floor moves with its events.
            "events": unstaged(managers.events.purge_across_tenants),
            "orchestrations": unstaged(managers.orchestrations.purge_across_tenants),
            "leases": unstaged(managers.leases.purge_across_tenants),
            # Not a purge: the leases past their expiry and the skew margin
            # end, the requests past their wait expire, and each free
            # resource is offered to its line, in every org with one due.
            "lease_sweep": managers.leases.sweep,
        },
        # The media purge's batch is its own: a whole one says there may be
        # more. So is the lease sweep's: the leases and requests one org's
        # pass ends.
        across_batches={"media": MEDIA_PURGE_BATCH, "lease_sweep": LEASE_SWEEP_BATCH},
        # Per tenant, and only in the tenants one read across tenants names as
        # due: the standing chores. The scaffold keeps no record per period,
        # so it runs none. A copy that keeps one wires here the chore that
        # opens the next period (its orchestration, started with its `period`)
        # and the read of the tenants where it is due (ADR 0089).
        chores={},
        chore_tenants=None,
        # The platform's size, counted across tenants once an interval and
        # kept as the tally the operator plane reads instead of counting.
        tally=managers.tenancy_operator.tally_size,
        handlers={
            WorkKind.NOOP: NoopHandlerImpl(),
            WorkKind.ORCHESTRATION: OrchestrationHandlerImpl(
                managers.orchestrations,
                {OrchestrationKind.NOOP: managers.orchestrations.step_noop},
            ),
            WorkKind.WAKE_PARKED: WakeParkedHandlerImpl(managers.orchestrations),
            WorkKind.DELETE_ACCOUNT: DeleteAccountHandlerImpl(
                managers.tenancy, container.identity_provider, identity_calls
            ),
            WorkKind.DELETE_ORG: DeleteOrgHandlerImpl(
                managers.tenancy, container.identity_provider, identity_calls
            ),
        },
        topics=container.infra.get_topics(),
        liveness=container.infra.get_cache(CacheScope.WORKER_LIVENESS),
        options=loop_options(container.settings, lane),
    )


def build_consumer(container: WorkerContainer) -> DeliveryConsumer:
    """The consumer of `Queues.WEBHOOKS`: each provider's deliveries, under
    the name the API queues them with."""
    return DeliveryConsumer(
        queues=container.infra.get_queues(),
        tenancy=container.managers.tenancy,
        providers={"identity": IdentityDeliveriesImpl(container.managers.events)},
        options=DeliveryOptions(worker_id=container.settings.worker_id),
    )


def boot(settings: MaintenanceSettings) -> None:
    """Logging first, then the process's name, the trust store, error
    reporting, and tracing: the order every process boots in."""
    configure_logging(settings.log_level, settings.log_json)
    # Second, before anything logs: every line this process writes carries the
    # service and the environment, whether or not reporting is configured.
    name_process(settings.service_name, settings.environment)
    install_trust_store()
    configure_error_reporting(
        settings.sentry_dsn, settings.environment, settings.service_name, settings.version
    )
    configure_tracing(
        settings.otel_endpoint,
        settings.service_name,
        timedelta(seconds=settings.otel_timeout_seconds),
    )


async def serve(lane: str | None) -> int:
    settings = MaintenanceSettings()
    boot(settings)
    container = WorkerContainer.build(settings)
    await container.start()
    loop = build_loop(container, lane)
    consumer = build_consumer(container)
    running = asyncio.get_running_loop()

    def stop() -> None:
        consumer.stop()
        loop.stop()

    for sig in (signal.SIGTERM, signal.SIGINT):
        running.add_signal_handler(sig, stop)
    # /metrics for Prometheus locally and the collector sidecar in the cloud,
    # /healthz for the container probe: the loop's own last beat, held in
    # memory, so a cache outage never restarts a worker.
    http = WorkerHttpServer(
        settings.metrics_host,
        settings.metrics_port,
        liveness_probe(loop),
        running,
    )
    http.start()
    try:
        # The claim loop, with the sweep, and the deliveries run side by
        # side; a stop ends both, the loop draining its items first.
        await asyncio.gather(loop.run(), consumer.run())
    finally:
        http.stop()
        await container.close()
    log.info("%s stopped", settings.worker_id)
    return 0


def liveness_probe(loop: WorkerLoop) -> Probe:
    async def alive() -> bool:
        return loop.alive()

    return alive


def health(settings: MaintenanceSettings) -> int:
    """The probe by hand: asks the serving process's `/healthz` on the metrics
    port and exits 0 on 200, 1 otherwise. The container healthcheck makes the
    same request without importing this package."""
    host = "127.0.0.1" if settings.metrics_host == "0.0.0.0" else settings.metrics_host
    url = f"http://{host}:{settings.metrics_port}/healthz"
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return 0 if response.status == 200 else 1
    except urllib.error.HTTPError as error:
        print(f"{url} answered {error.code}", file=sys.stderr)
    except OSError as error:
        print(f"{url} is unreachable: {error}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="acme-maintenance")
    sub = parser.add_subparsers(dest="command", required=True)
    p_serve = sub.add_parser("serve", help="run the worker loop")
    p_serve.add_argument("--lane", help="the lane to claim from; defaults to ACME_WORKER_LANE")
    sub.add_parser("health", help="exit 0 while the serving worker answers /healthz with 200")
    args = parser.parse_args(argv)
    if args.command == "health":
        return health(MaintenanceSettings())
    return asyncio.run(serve(args.lane))


if __name__ == "__main__":
    sys.exit(main())
