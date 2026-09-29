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
from acme.om.opcontext import AppContext, AppType, RequestContext
from acme.om.orchestrations.types.orchestration import OrchestrationKind
from acme.om.work.types.work_item import WorkKind
from acme.workers.maintenance.accounts import (
    DeleteAccountHandlerImpl,
    DeleteOrgHandlerImpl,
    UnassignTasksHandlerImpl,
)
from acme.workers.maintenance.container import MEDIA_PURGE_BATCH, WorkerContainer
from acme.workers.maintenance.deliveries import DeliveryConsumer, DeliveryOptions
from acme.workers.maintenance.handler import NoopHandlerImpl, SyncSeatsHandlerImpl
from acme.workers.maintenance.health import Probe, WorkerHttpServer
from acme.workers.maintenance.loop import AcrossStep, LoopOptions, WorkerLoop
from acme.workers.maintenance.orchestrations import (
    OrchestrationHandlerImpl,
    WakeParkedHandlerImpl,
)
from acme.workers.maintenance.reminders import TaskReminderHandlerImpl
from acme.workers.maintenance.settings import MaintenanceSettings
from acme.workers.maintenance.slack_inbound import (
    InboundOptions,
    SlackInboundConsumer,
    SlackInboundHandler,
)
from acme.workers.maintenance.slack_posts import SlackPostHandlerImpl

log = logging.getLogger(__name__)


def loop_options(settings: MaintenanceSettings, lane: str | None = None) -> LoopOptions:
    return LoopOptions(
        worker_id=settings.worker_id,
        lane=lane or settings.worker_lane,
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
    return WorkerLoop(
        work=container.managers.work,
        outbox=container.managers.outbox,
        # Per tenant, what only a tenant deleted past its retention has: every
        # row of it goes. Any other tenant costs these nothing.
        purges={
            "tasks": container.managers.tasks.purge_tenant,
            # Every file's object, then its row.
            "media": container.managers.media.purge_tenant,
            "tenancy": container.managers.tenancy.purge_tenant,
            "events": container.managers.events.purge_tenant,
            "billing": container.managers.billing.purge_tenant,
            "slack": container.managers.slack.purge_tenant,
            "orchestrations": container.managers.orchestrations.purge_tenant,
        },
        # Once a pass, across every tenant: each namespace's rows past their
        # retention.
        across={
            # A deleted task's attachments, under its tenant's context, then the task.
            "tasks": container.managers.tasks.purge_across_tenants,
            # A deleted file's object, then its row; an abandoned upload's too.
            "media": unstaged(container.managers.media.purge_across_tenants),
            "tenancy": unstaged(container.managers.tenancy.purge_across_tenants),
            "idempotency": unstaged(container.managers.idempotency.purge_across_tenants),
            # The trim: each tenant's floor moves with its events.
            "events": unstaged(container.managers.events.purge_across_tenants),
            "billing": unstaged(container.managers.billing.purge_across_tenants),
            "slack": unstaged(container.managers.slack.purge_across_tenants),
            "orchestrations": unstaged(container.managers.orchestrations.purge_across_tenants),
        },
        # The media purge's batch is its own: a whole one says there may be more.
        across_batches={"media": MEDIA_PURGE_BATCH},
        # A record kept per day opens here: the org's cleanup of old done
        # tasks. Its unique key makes every sweep after the day's first a no-op.
        # The respace gives short ranks back to a run of open tasks whose
        # ranks grew long.
        chores={
            "cleanup": container.managers.tasks.open_cleanup,
            "respace": container.managers.tasks.respace_ranks,
        },
        # The tenants the chores run in: one read a pass across tenants finds
        # those with an archivable task or a long rank, so a tenant with
        # neither costs the pass nothing.
        chore_tenants=container.managers.tasks.tenants_with_chores,
        # The platform's size, counted across tenants once an interval and
        # kept as the tally the operator plane reads instead of counting.
        tally=container.managers.tenancy_operator.tally_size,
        handlers={
            WorkKind.NOOP: NoopHandlerImpl(),
            WorkKind.SYNC_SEATS: SyncSeatsHandlerImpl(
                container.managers.tenancy, container.managers.billing
            ),
            WorkKind.TASK_REMINDER: TaskReminderHandlerImpl(container.managers.tasks),
            WorkKind.SLACK_POST: SlackPostHandlerImpl(
                container.managers.tasks, container.managers.slack, container.slack
            ),
            WorkKind.ORCHESTRATION: OrchestrationHandlerImpl(
                container.managers.orchestrations,
                {
                    OrchestrationKind.TASK_IMPORT: container.managers.tasks.step_import,
                    OrchestrationKind.TASK_CLEANUP: container.managers.tasks.step_cleanup,
                },
            ),
            WorkKind.WAKE_PARKED: WakeParkedHandlerImpl(container.managers.orchestrations),
            WorkKind.DELETE_ACCOUNT: DeleteAccountHandlerImpl(
                container.managers.tenancy,
                container.managers.billing,
                container.managers.slack,
                container.identity_provider,
            ),
            WorkKind.UNASSIGN_TASKS: UnassignTasksHandlerImpl(container.managers.tasks),
            WorkKind.DELETE_ORG: DeleteOrgHandlerImpl(
                container.managers.tenancy,
                container.managers.billing,
                container.managers.slack,
                container.identity_provider,
            ),
        },
        topics=container.infra.get_topics(),
        liveness=container.infra.get_cache(CacheScope.WORKER_LIVENESS),
        options=loop_options(container.settings, lane),
    )


def build_consumer(container: WorkerContainer) -> DeliveryConsumer:
    return DeliveryConsumer(
        queues=container.infra.get_queues(),
        billing=container.managers.billing,
        tenancy=container.managers.tenancy,
        options=DeliveryOptions(worker_id=container.settings.worker_id),
    )


def build_inbound(container: WorkerContainer) -> SlackInboundConsumer:
    """The consumer of the `slack` queue: what the API checked, acknowledged,
    and queued, handled under the request stage it mints."""
    settings = container.settings
    handler = SlackInboundHandler(
        container.managers.slack,
        container.managers.tasks,
        container.managers.tenancy,
        container.slack,
        AppContext(type=AppType.SLACK, version=f"slack@{settings.worker_id}"),
        settings.portal_url,
    )
    options = InboundOptions(
        visibility=timedelta(seconds=settings.slack_inbound_visibility_seconds)
    )
    return SlackInboundConsumer(container.infra.get_queues(), handler, options)


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
    inbound = build_inbound(container)
    consumer = build_consumer(container)
    running = asyncio.get_running_loop()

    def stop() -> None:
        inbound.stop()
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
    consuming = asyncio.create_task(inbound.run(), name="slack-inbound")
    try:
        # The claim loop and the processor's deliveries run side by side; a
        # stop ends both, the loop draining its items first.
        await asyncio.gather(loop.run(), consumer.run())
    finally:
        consuming.cancel()
        await asyncio.gather(consuming, return_exceptions=True)
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
