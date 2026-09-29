"""What an org's plan is, and what it entitles the org to."""

from enum import StrEnum

from acme.om.base import Platform


class Plan(StrEnum):
    FREE = "free"
    PRO = "pro"
    TEAM = "team"
    MAX = "max"


class Lever(StrEnum):
    """Each thing a plan bounds. A lever is never a hidden feature: every org
    sees every feature, and meeting a bound is a refusal that names the
    lever, the plan, the bound, and the plan that lifts it."""

    MEMBERS = "members"
    API_KEYS = "api_keys"
    ACTIVE_TASKS = "active_tasks"
    STORAGE = "storage"


class PlanLimits(Platform):
    members: int | None
    """Active memberships the org holds at once; None is no bound."""
    api_keys: bool
    """Whether an api key may be made, and whether one authenticates."""
    active_tasks: int | None
    """Tasks neither done nor deleted; None is no bound."""
    storage_bytes: int
    """What the org's files may hold together."""


class PlanPrice(Platform):
    """A plan's monthly price, the shape the processor's price has: a flat
    amount that covers up to `included_seats` seats, and a price per seat for
    every seat once the count is past them (a volume tier: the whole count is
    priced at the tier it reaches)."""

    lookup_key: str | None
    flat_cents: int
    included_seats: int | None = None
    per_seat_cents: int = 0
