"""The handler of NOOP, the kind that keeps the loop honest.

Each handler names the permissions its calls take (`REQUIRES`), and the
tests hold every role that may ask for the kind (`WORK_ENQUEUE_PERMISSIONS`)
to them: nobody reaches through the queue what they could not do directly."""

import logging
from typing import ClassVar

from acme.om.opcontext import OpContext, Permission
from acme.om.work.types.handler import WorkHandlerInterface
from acme.om.work.types.work_item import WorkItem

log = logging.getLogger(__name__)


class NoopHandlerImpl(WorkHandlerInterface):
    """Idempotent by construction: handling the same item twice changes nothing.
    It holds nothing per item: it runs for the life of the worker."""

    REQUIRES: ClassVar[tuple[Permission, ...]] = ()

    async def handle(self, ctx: OpContext, item: WorkItem) -> None:
        log.info(
            "noop %s for %s in org %s (attempt %d)",
            item.id,
            item.target_id,
            ctx.org_id,
            item.attempts,
        )
