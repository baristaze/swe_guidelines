from acme.infra.flags import FlagsInterface
from acme.om.context import TenantContext
from acme.services.api.services.flags import FlagsServiceInterface
from acme.services.api.types.flags import FlagsView


class FlagsServiceImpl(FlagsServiceInterface):
    def __init__(self, flags: FlagsInterface) -> None:
        self._flags = flags

    async def get_flags(self, ctx: TenantContext) -> FlagsView:
        evaluated = await self._flags.evaluate(ctx.org_id, ctx.user_id)
        return FlagsView(
            flags={flag.value: on for flag, on in sorted(evaluated.for_clients().items())}
        )
