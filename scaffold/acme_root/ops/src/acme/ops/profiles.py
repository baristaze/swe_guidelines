"""The four traffic profiles. A profile sets how many tenants, how many people
in each, how many sessions run at once, how long a person thinks between
steps, and how long a run lasts when the caller names no duration. The
numbers are this system's and nothing else reads them; a stress scenario
names a profile and may override the duration."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Profile:
    name: str
    orgs: int
    members_per_org: int
    concurrency: int
    think_seconds: tuple[float, float]
    duration_seconds: int

    @property
    def people(self) -> int:
        return self.orgs * self.members_per_org


LIGHT = Profile(
    "light", orgs=1, members_per_org=2, concurrency=2, think_seconds=(0.8, 1.6), duration_seconds=60
)
"""The gate: proves the wiring, says nothing about capacity. Two sessions at
once over the seeded org, and two sign-ins for the whole run."""

REGULAR = Profile(
    "regular",
    orgs=3,
    members_per_org=5,
    concurrency=8,
    think_seconds=(0.5, 1.2),
    duration_seconds=300,
)
"""A working day for a few teams. A run signs one person in per worker, so
eight sign-ins from one address, under the login rate limit of ten a
minute."""

HEAVY = Profile(
    "heavy",
    orgs=10,
    members_per_org=10,
    concurrency=32,
    think_seconds=(0.2, 0.6),
    duration_seconds=600,
)
"""Every team busy at once. Thirty-two sign-ins are past the login rate limit
of one address, so a run of this profile wants more than one generator."""

STRESS = Profile(
    "stress",
    orgs=25,
    members_per_org=20,
    concurrency=128,
    think_seconds=(0.05, 0.2),
    duration_seconds=900,
)
"""More than the system is sized for, to find where it bends."""

PROFILES: dict[str, Profile] = {p.name: p for p in (LIGHT, REGULAR, HEAVY, STRESS)}


def profile_named(name: str) -> Profile:
    try:
        return PROFILES[name]
    except KeyError:
        raise ValueError(f"unknown profile {name!r}; one of {', '.join(PROFILES)}") from None
