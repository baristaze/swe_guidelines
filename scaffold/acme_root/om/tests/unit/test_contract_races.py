"""A contract case that puts two callers at one guard runs them through
`race` (contracts/racing.py), never `asyncio.gather` of its own.

The guard is the same in both impls; the run is not. Under the engine the
callers overlap inside the statement. Under a memory impl they do not: every
conditional write there reads and writes under one lock with no await in
between, so the event loop runs one caller to its end before it starts the
other, and what that run proves is the conditional write and the refusal that
follows it. `race` is where the difference is written down and measured, so a
bare gather in a contract module is a case that reads as a race half its runs
never ran.

The scan holds the rule over every contract module; the two cases under it
hold `race` to reporting which run it was."""

import asyncio
from pathlib import Path

from contracts.racing import race

CONTRACTS = Path(__file__).resolve().parents[1] / "contracts"
HELPER = CONTRACTS / "racing.py"


def test_every_contract_module_races_through_the_helper() -> None:
    assert HELPER.exists(), "the helper that says what a race case proves is gone"
    reaching = [
        module.name
        for module in sorted(CONTRACTS.glob("*.py"))
        if module != HELPER and "asyncio.gather" in module.read_text()
    ]
    assert reaching == [], (
        f"{reaching}: run the callers through contracts.racing.race, which reports "
        "what the run was, instead of gathering them here"
    )


async def test_callers_that_never_await_do_not_overlap() -> None:
    # The memory impl's shape: the whole conditional write runs with no await
    # in it, so the loop finishes one caller before it starts the next.
    order: list[str] = []

    async def caller(name: str) -> str:
        order.append(f"{name} in")
        order.append(f"{name} out")
        return name

    run = await race(caller("a"), caller("b"))
    assert run.admitted == ["a", "b"]
    assert not run.overlapped
    assert order == ["a in", "a out", "b in", "b out"]


async def test_callers_that_suspend_overlap() -> None:
    # The engine's shape: each caller suspends on its socket inside the guard.
    order: list[str] = []

    async def caller(name: str) -> str:
        order.append(f"{name} in")
        await asyncio.sleep(0)
        order.append(f"{name} out")
        return name

    run = await race(caller("a"), caller("b"))
    assert run.admitted == ["a", "b"]
    assert run.overlapped
    assert order == ["a in", "b in", "a out", "b out"]
