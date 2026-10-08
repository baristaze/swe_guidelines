"""The deployed impl: every flag evaluated in this process through
OpenFeature, the vendor-neutral flag API, over the provider of the vendor
the settings name. OpenFeature and the vendor's SDK are imported in this
package and nowhere else, so a manager holds `FlagsInterface` and never
knows the vendor."""

import asyncio
import logging
from collections.abc import Callable
from uuid import UUID

from openfeature import api
from openfeature.evaluation_context import EvaluationContext
from openfeature.provider import FeatureProvider

from acme.infra.base import new_id
from acme.infra.flags import FLAGS, FlagSet, FlagsInterface

log = logging.getLogger(__name__)


def evaluation_context(org_id: UUID, user_id: UUID | None) -> EvaluationContext:
    """The targeting key is the org, so a percentage rollout is sticky per
    org. `org_id` and `user_id` are attributes, so a rule targets either.
    `kind` names the context for a provider that types its contexts, as
    LaunchDarkly does; any other reads it as one more attribute."""
    attributes: dict[str, str] = {"kind": "org", "org_id": str(org_id)}
    if user_id is not None:
        attributes["user_id"] = str(user_id)
    return EvaluationContext(targeting_key=str(org_id), attributes=attributes)


class FlagsOpenFeatureImpl(FlagsInterface):
    """`provider` builds the vendor's provider at start, off the event loop,
    since a vendor's client may block while it connects. Until it is ready,
    and whenever it fails, OpenFeature answers the default each call names:
    the code's."""

    def __init__(self, vendor: str, provider: Callable[[], FeatureProvider]) -> None:
        self._vendor = vendor
        self._build_provider = provider
        self._provider: FeatureProvider | None = None
        # A domain of its own in OpenFeature's process-wide registry, so two
        # roots in one process (two tests) never share a provider.
        self._domain = f"acme-flags-{new_id()}"
        self._client = api.get_client(domain=self._domain)

    async def evaluate(self, org_id: UUID, user_id: UUID | None = None) -> FlagSet:
        context = evaluation_context(org_id, user_id)
        values = {}
        for flag, spec in FLAGS.items():
            values[flag] = await self._client.get_boolean_value_async(
                flag.value, spec.default, context
            )
        return FlagSet(values=values)

    def describe(self) -> str:
        return f"flags={self._vendor}(openfeature)"

    async def start(self) -> None:
        """A provider that is not ready within its own bound leaves the
        process running on the code's defaults, and a warning says so; it
        takes over once it is."""
        provider = await asyncio.to_thread(self._build_provider)
        self._provider = provider
        try:
            await asyncio.to_thread(api.set_provider_and_wait, provider, self._domain)
        except Exception as error:  # any failure means "not ready"
            log.warning(
                "flags provider %s not ready (%s); every flag reads its default until it is",
                self._vendor,
                type(error).__name__,
            )

    async def close(self) -> None:
        if self._provider is not None:
            await asyncio.to_thread(self._provider.shutdown)
            self._provider = None
