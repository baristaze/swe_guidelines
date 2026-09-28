"""A rehearsal: a scenario run as it will really run, only small.

A run that takes hours and many dollars should not be the first time its
pipeline runs end to end. `run.py --rehearsal` runs the scenario with
every bound cut small, so a few dollars prove that the run starts, moves
from phase to phase, commits, archives, fetches the output back, runs the
gates, and has the judges answer. The scores mean nothing.

`scenario` returns the scenario a rehearsal runs. It keeps every prompt,
the runtime, the gates, the judges and their models, and the references,
and it never raises a bound the scenario sets:

- the subject runs on the cheapest model the matrix prices for its
  provider (`cheapest`);
- every session of a skill subject, each phase or its one session, gets
  at most `MAX_TURNS` turns, `SESSION_USD` US dollars, and a timeout of
  `TIMEOUT_S`. A phase that hits a bound hands on to the next one, since
  a rehearsal's phases all end at a bound and every phase must start;
- a command subject gets the same timeout, and each gate at most
  `GATE_TIMEOUT_S`;
- agentic judges get the stub budget, `JUDGE_BUDGET`, each bound the
  smaller of the scenario's and the stub's;
- the run repeats once, and its spend cap, over the subject and the
  judges together, is `MAX_SPEND_USD`.

A rehearsal runs the preflight first, with the `checkout` check as a
skip: a rehearsal is never checked in, and a change to the harness is
worth rehearsing before it is committed. Its `run.json` and its
`results.json` are marked `rehearsal`, and `make runs` refuses a run
folder so marked.

A rehearsal checks its output folder after every phase. A phase this
small can end before it makes the folder, and the phases after it that
start in it, the checkpoints, the archive, and the gates would then have
nothing to run in. So the rehearsal makes the folder, empty, and the run
notes it (`MAKE_OUTPUT`).

A rehearsal ends `completed` only when every step it exists to prove
happened (`outcome`). One where a step did not ends `incomplete`, and
names each step under `missing`.

Like every harness module, this one imports the standard library only.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from . import agentic as A
from . import judge as J
from . import providers as P
from .scenario import Scenario

# The run's spend cap, over the subject and the judges together. A flag can
# lower it and never raise it.
MAX_SPEND_USD = 5.0
# Each session's bounds: its turns, its spend in US dollars, and its timeout,
# the backstop. Each gate's timeout on the final tree.
MAX_TURNS = 5
SESSION_USD = 0.5
TIMEOUT_S = 1800
GATE_TIMEOUT_S = 600
# The stub budget of each agentic judgement: enough to read and to submit.
JUDGE_BUDGET = A.Budget(tool_calls=3, input_tokens=60_000, wall_s=900.0, submits=2, max_usd=0.5, max_output_tokens=8_000)
# Where the subject runs, in its workspace: make the output folder when a
# phase left none, and say so. Argument: the folder.
MAKE_OUTPUT = '[ -d "$1" ] && exit 0; mkdir -p -- "$1" && echo made'


def cheapest(matrix: dict[str, dict[str, Any]], provider: str) -> str | None:
    """The model the matrix prices lowest for a provider, input price first, then output; None when it prices none."""
    priced = [(p["input"], p["output"], m) for m in J.models_for(matrix, provider) if (p := J.price_for(matrix, provider, m))]
    return min(priced)[2] if priced else None


def budget(given: A.Budget | None) -> A.Budget | None:
    """The stub budget of an agentic judgement: each bound the smaller of the scenario's and the stub's."""
    if given is None:
        return None
    return A.Budget(**{f.name: min(getattr(given, f.name), getattr(JUDGE_BUDGET, f.name)) for f in dataclasses.fields(A.Budget)})


def scenario(scn: Scenario, matrix: dict[str, dict[str, Any]]) -> Scenario:
    """The scenario as a rehearsal runs it: the same prompts, runtime, gates, and judges, with every bound cut small."""
    subject = scn.subject
    phases = [
        dataclasses.replace(
            p,
            max_turns=min(p.max_turns, MAX_TURNS),
            max_usd=min(p.max_usd, SESSION_USD),
            timeout_s=min(p.timeout_s, TIMEOUT_S),
            on_cap="continue",
        )
        for p in subject.phases
    ]
    model = subject.model
    if scn.kind == "skill":
        model = cheapest(matrix, "anthropic") or model
    elif scn.kind == "qa":
        model = cheapest(matrix, P.name(P.parse(subject.provider or "anthropic"))) or model
    subject = dataclasses.replace(
        subject,
        model=model,
        max_turns=min(subject.max_turns, MAX_TURNS),
        max_usd=None if subject.max_usd is None else min(subject.max_usd, SESSION_USD),
        timeout_s=min(subject.timeout_s, TIMEOUT_S),
        gate_timeout_s=min(subject.gate_timeout_s, GATE_TIMEOUT_S),
        phases=phases,
    )
    judges = dataclasses.replace(scn.judges, budget=budget(scn.judges.budget))
    cap = MAX_SPEND_USD if scn.max_spend_usd is None else min(scn.max_spend_usd, MAX_SPEND_USD)
    return dataclasses.replace(scn, subject=subject, judges=judges, repeat=1, max_spend_usd=cap)


def missing(scn: Scenario, repeat: Any, archived: bool | None) -> list[str]:
    """The steps a repeat was to prove and did not: a checkpoint, the archive, its fetch, the gates, a judge's answer.

    `archived` is whether the archive command succeeded where the subject
    ran, or None when there was no checkpoint to archive.
    """
    out: list[str] = []
    if scn.subject.output:
        out += [f"phase {p['name']} left no checkpoint" for p in repeat.phases or [] if not p.get("checkpoint")]
        if archived is False:
            out.append("the archive of the output failed")
        elif archived and repeat.archive is None:
            out.append("no archive of the output came back from the runtime")
        if scn.subject.gates and repeat.gates is None:
            out.append("the gates did not run")
    if not any(j.status == "ok" for j in repeat.judgements):
        out.append("no judge answered")
    return out


def outcome(
    scn: Scenario, repeats: list[Any], failed: bool, cap: float | None, archived: dict[int, bool | None]
) -> dict[str, Any]:
    """How a rehearsal ended, the steps it did not prove, and where its money went, in the order it was spent.

    It ended `capped` when the run's spend cap kept a phase from running,
    `failed` when the subject failed, `incomplete` when a step it exists to
    prove did not happen, and `completed` otherwise. `archived` holds each
    repeat's archive command, by index, as `missing` reads it.
    """
    status = "capped" if any(r.cut_short for r in repeats) else "failed" if failed else "completed"
    steps = [f for r in repeats for f in missing(scn, r, archived.get(r.index))] if status == "completed" else []
    spent: list[dict[str, Any]] = []
    for repeat in repeats:
        for phase in repeat.phases or []:
            usd = phase.get("cost_usd") if phase.get("cost_usd") is not None else phase.get("estimated_usd")
            spent.append({"what": f"phase {phase['name']}", "usd": round(float(usd or 0.0), 4)})
        if repeat.phases is None and repeat.subject_cost_usd is not None:
            spent.append({"what": "subject", "usd": round(repeat.subject_cost_usd, 4)})
        # A judge that spent nothing, such as one with no key, is named in the summary, not here.
        spent += [{"what": f"judge {j.provider}", "usd": round(j.cost_usd or 0.0, 4)} for j in repeat.judgements if j.usage]
    return {
        "status": "incomplete" if steps else status,
        "missing": steps,
        "max_spend_usd": cap,
        "spent_usd": round(sum(s["usd"] for s in spent), 4),
        "spent": spent,
    }


def says(record: dict[str, Any]) -> str:
    """A rehearsal's outcome as the console and the report say it."""
    where = ", ".join(f"{s['what']} ${s['usd']:.4f}" for s in record["spent"]) or "nothing"
    cap = f" of its ${record['max_spend_usd']:g} cap" if record.get("max_spend_usd") is not None else ""
    said = f"the rehearsal ended {record['status']}, having spent ${record['spent_usd']:.4f}{cap}: {where}"
    return said + (f"; it did not prove: {', '.join(record['missing'])}" if record.get("missing") else "")
