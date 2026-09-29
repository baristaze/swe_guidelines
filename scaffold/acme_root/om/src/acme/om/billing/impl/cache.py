"""The org's billing account, cached per org under the tenant's generation.

The key of a cached account carries the org's generation, a counter in the
same scope. Every write of the account bumps that counter with one
`increment` after its commit, so the entry a read cached before the write
is orphaned at once and expires by its TTL. The TTL is the backstop: a bump
that does not land leaves the old entry readable until then, and never
longer.

The cache fails open. A miss, an entry this build cannot read, and a Valkey
that cannot be reached are each a read of storage. Only the account is
cached; what the caller may see of it is decided above this, on every call.
"""

from collections.abc import Awaitable, Callable
from datetime import timedelta
from uuid import UUID

from pydantic import ValidationError

from acme.infra.cache import CacheInterface
from acme.om.billing.types.account import BillingAccount

GENERATION = "generation"
GENERATION_TTL = timedelta(days=1)
"""How long a generation counts from its first bump. Far longer than any
entry lives, so a counter that ends and starts again at one finds no entry
of the last window still readable past the account's own TTL."""

NO_ACCOUNT = b"null"
"""An org with no account yet, on Free: cached too, since it is most orgs."""


class AccountCache:
    def __init__(self, cache: CacheInterface, ttl: timedelta) -> None:
        self._cache = cache
        self._ttl = ttl

    async def read(
        self, org_id: UUID, load: Callable[[], Awaitable[BillingAccount | None]]
    ) -> BillingAccount | None:
        """The account from the cache, else from `load`, which is then
        cached. The generation is read before storage, so an account read
        before a write and put after its bump lands under the generation the
        bump left behind."""
        key = await self._key(org_id)
        cached = await self._cache.get(org_id, key)
        if cached is not None:
            try:
                return decoded(cached)
            except ValidationError:
                pass  # another build's shape: a miss
        account = await load()
        await self._cache.put(org_id, key, encoded(account), self._ttl)
        return account

    async def changed(self, org_id: UUID) -> None:
        await account_changed(self._cache, org_id)

    async def _key(self, org_id: UUID) -> str:
        generation = await self._cache.get(org_id, GENERATION)
        count = generation.decode() if generation is not None else "0"
        return f"account:{count if count.isdigit() else 0}"


async def account_changed(cache: CacheInterface, org_id: UUID) -> None:
    """Called after a write of the org's account commits: one `increment`
    of its generation, which orphans every account cached before it."""
    await cache.increment(org_id, GENERATION, GENERATION_TTL)


def encoded(account: BillingAccount | None) -> bytes:
    return NO_ACCOUNT if account is None else account.model_dump_json().encode()


def decoded(value: bytes) -> BillingAccount | None:
    return None if value == NO_ACCOUNT else BillingAccount.model_validate_json(value)
