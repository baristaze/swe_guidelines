from uuid import UUID

from acme.infra.flags import FlagSet, FlagsInterface, code_defaults


class FlagsDefaultsImpl(FlagsInterface):
    """No provider: every flag reads the default its declaration gives, and
    the boot line says so. What `ACME_FLAGS_BACKEND=none` holds."""

    async def evaluate(self, org_id: UUID, user_id: UUID | None = None) -> FlagSet:
        return code_defaults()

    def describe(self) -> str:
        return "flags=none(every flag reads its default)"

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None
