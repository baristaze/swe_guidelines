"""A run and its resumes: one measurement, however many run folders it took.

A run that resumes another, after a phase or into its judges, names that
run under `source` in its records, and so does a run that judges
another's output again. So a run the harness stopped, and the runs that
finished it, are run folders chained by `source.run_id`, and together
they are one measurement: its chain.

A chain's stages are what each of its folders ran, per repeat: each
phase, or a subject's one session, and the judges. Its cost is what its
folders spent in all. A folder counts only what it ran, never a carried
phase or a carried judgement, so the sum counts each stage once.

The chain is read from each folder's own records, so a folder recorded
before chains were is held too. A folder's source is the folder of that
name beside it, in its scenario's folder, where `resume` and `judge`
write a new run folder when `--out` names no other root.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .judge import half_up
from .results import failed


def record(folder: Path, file: str = "results.json") -> dict[str, Any]:
    """A JSON object a run folder holds, `results.json` by default; empty when the file is not there or holds no object."""
    try:
        data = json.loads((folder / file).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def source_of(folder: Path) -> str | None:
    """The run folder a run names as its source, by `source.run_id` in its `results.json`, else its `run.json`."""
    for file in ("results.json", "run.json"):
        source = record(folder, file).get("source")
        if isinstance(source, dict) and isinstance(source.get("run_id"), str) and source["run_id"]:
            return str(source["run_id"])
    return None


def lineage(folder: Path) -> tuple[list[Path], str | None]:
    """The folders of a run's chain, oldest first and ending with `folder`, and why the chain breaks, or None.

    Each folder's source is the folder of that name beside it. A source
    that is not there, a name that is not one folder's, and a source
    already in the chain break it; the chain is then the folders it
    reached.
    """
    chain = [folder]
    while (name := source_of(chain[0])) is not None:
        if "/" in name or "\\" in name or name in (".", ".."):
            return chain, f"{chain[0].name} names {name!r} as its source, which is no run folder's name"
        if name in {f.name for f in chain}:
            return chain, f"{chain[0].name} names {name} as its source, and {name} is later in its chain"
        prior = folder.parent / name
        if not prior.is_dir():
            return chain, f"{chain[0].name} names {name} as its source, and {folder.parent.name} holds no such run folder"
        chain.insert(0, prior)
    return chain, None


def ran_no_subject(data: dict[str, Any]) -> bool:
    """Whether a folder judged another run's output, again or in part, and ran no subject: its source names no phase."""
    source = data.get("source")
    return isinstance(source, dict) and "after" not in source


def number(value: Any) -> float | None:
    """A recorded amount, or None when the record holds none."""
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def subject_stages(name: str, index: int, repeat: dict[str, Any]) -> list[dict[str, Any]]:
    """The stages of one repeat's subject that a folder ran: each phase not carried, or its one session."""
    phases = repeat.get("phases")
    if isinstance(phases, list):
        out = []
        for phase in phases:
            if not isinstance(phase, dict) or phase.get("carried") or not isinstance(phase.get("name"), str):
                continue
            cost = number(phase.get("cost_usd"))
            stage = {
                "repeat": index,
                "stage": phase["name"],
                "run_id": name,
                "status": str(phase.get("status") or ""),
                "cost_usd": cost if cost is not None else number(phase.get("estimated_usd")),
                "time_s": number(phase.get("wall_s")),
            }
            if cost is None and stage["cost_usd"] is not None:
                stage["estimated"] = True
            out.append(stage)
        return out
    status = repeat.get("exit_status")
    if not isinstance(status, dict):
        return []
    return [
        {
            "repeat": index,
            "stage": "subject",
            "run_id": name,
            "status": "failed" if failed(status) else "ok",
            "cost_usd": number(repeat.get("subject_cost_usd")),
            "time_s": number(status.get("duration_s")),
        }
    ]


def judges_stage(name: str, index: int, repeat: dict[str, Any]) -> dict[str, Any] | None:
    """The judges one repeat ran in a folder, the carried ones left out; None when it ran none.

    Its status is `ok` when every judge it ran answered, `failed` when
    none did, and `missed` otherwise, with those that did not answer. An
    agentic repeat's judges run at once, so their time is the longest
    judgement's; one-shot judges run one after another, so theirs is the
    sum.
    """
    given = repeat.get("judgements")
    ran = [j for j in given if isinstance(j, dict) and not j.get("carried")] if isinstance(given, list) else []
    if not ran:
        return None
    judges = [str(j.get("provider") or "") for j in ran]
    missed = [str(j.get("provider") or "") for j in ran if j.get("status") != "ok"]
    times = [t for j in ran if (t := number(j.get("latency_s"))) is not None]
    agentic = all(isinstance(j.get("judged"), dict) for j in ran)
    return {
        "repeat": index,
        "stage": "judges",
        "run_id": name,
        "status": "ok" if not missed else "failed" if len(missed) == len(ran) else "missed",
        "judges": judges,
        "missed": missed,
        "cost_usd": round(sum(number(j.get("cost_usd")) or 0.0 for j in ran), 6),
        "time_s": (max(times) if agentic else round(sum(times), 3)) if times else None,
    }


def stages(name: str, data: dict[str, Any]) -> list[dict[str, Any]]:
    """What one folder ran, repeat by repeat: its subject's stages, unless it ran no subject, then its judges."""
    out: list[dict[str, Any]] = []
    listed = data.get("repeats")
    repeats = [r for r in listed if isinstance(r, dict) and isinstance(r.get("index"), int)] if isinstance(listed, list) else []
    for repeat in sorted(repeats, key=lambda r: r["index"]):
        if not ran_no_subject(data):
            out += subject_stages(name, repeat["index"], repeat)
        judged = judges_stage(name, repeat["index"], repeat)
        if judged is not None:
            out.append(judged)
    return out


def chain(records: list[tuple[str, dict[str, Any]]], broken: str | None = None) -> dict[str, Any]:
    """The chain of the folders `records` holds, oldest first: each folder with what it spent, each stage, and the total.

    A folder's spend is its `spend.total_usd`. The total is their sum, and
    it is a lower bound, `at_least`, when a folder recorded no spend, a
    model had no price, or the chain breaks before its first folder.
    """
    folders: list[dict[str, Any]] = []
    every: list[dict[str, Any]] = []
    total = 0.0
    unpriced: set[str] = set()
    for name, data in records:
        spend = data.get("spend")
        spent = number(spend.get("total_usd")) if isinstance(spend, dict) else None
        if spent is not None:
            total += spent
        if isinstance(spend, dict) and isinstance(spend.get("unpriced"), list):
            unpriced |= {str(m) for m in spend["unpriced"]}
        folders.append({"run_id": name, "started_at": data.get("started_at"), "total_usd": spent})
        every += stages(name, data)
    out: dict[str, Any] = {
        "folders": folders,
        "stages": every,
        "total_usd": round(total, 4),
        "unpriced": sorted(unpriced),
        "at_least": bool(unpriced) or broken is not None or any(f["total_usd"] is None for f in folders),
    }
    if broken is not None:
        out["broken"] = broken
    return out


def of(folder: Path) -> dict[str, Any]:
    """The chain a run folder ends, read from the records of each of its folders."""
    folders, broken = lineage(folder)
    return chain([(f.name, record(f)) for f in folders], broken)


def usd(value: float) -> str:
    """Dollars as a row of the index shows them: to the cent, halves rounded up."""
    return f"${half_up(value, 2):,.2f}"


def cost_text(found: dict[str, Any]) -> str:
    """A chain's total as its row's Cost (USD) cell reads: "—" when no folder recorded a spend, "at least" for a lower bound."""
    if all(f["total_usd"] is None for f in found["folders"]):
        return "—"
    return f"at least {usd(found['total_usd'])}" if found["at_least"] else usd(found["total_usd"])
