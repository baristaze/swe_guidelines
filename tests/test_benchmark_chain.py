"""benchmark/harness/chain.py: a run and its resumes, one measurement read from each folder's own records."""

import json
from pathlib import Path

import pytest

from harness import chain as CH

FIRST = "20260927-233327-create-full-system-11435123"
RESUMED = "20260928-065025-create-full-system-0a609288"
JUDGED = "20260928-104821-create-full-system-447562f8"


def phase(name, status, cost=None, estimated=None, wall=None, carried=False):
    out = {"name": name, "status": status, "cost_usd": cost, "estimated_usd": estimated, "wall_s": wall}
    return {**out, "carried": True} if carried else out


def judged(provider, status, cost, latency, carried=False):
    out = {"provider": provider, "status": status, "cost_usd": cost, "latency_s": latency, "judged": {}}
    return {**out, "carried": True} if carried else out


# The fields the chain reads of a run of create-full-system the harness stopped after its scaffold, its resume
# after the scaffold, and the resume of its judges, as their results.json recorded them.
RECORDS = {
    FIRST: {
        "run_id": FIRST,
        "started_at": "2026-09-28T06:33:28Z",
        "spend": {"total_usd": 82.5281, "unpriced": []},
        "repeats": [
            {
                "index": 0,
                "exit_status": {"code": 0},
                "phases": [phase("scaffold", "incomplete", 82.528093, 99.354976, 9739.373)],
                "judgements": [],
            }
        ],
    },
    RESUMED: {
        "run_id": RESUMED,
        "started_at": "2026-09-28T13:50:26Z",
        "spend": {"total_usd": 87.7648, "unpriced": []},
        "source": {"run_id": FIRST, "after": "scaffold"},
        "repeats": [
            {
                "index": 0,
                "exit_status": {"code": 0},
                "phases": [
                    phase("scaffold", "incomplete", 82.528093, 99.354976, 9739.373, carried=True),
                    phase("mvp", "ok", 22.631202, 26.780531, 3333.834),
                    phase("review", "ok", 20.350875, 24.879358, 630.849),
                    phase("close", "ok", 15.301141, 16.586912, 1145.569),
                ],
                "judgements": [
                    judged("anthropic", "ok", 7.415676, 165.29),
                    judged("openai", "error", 1.660084, 72.153),
                    judged("gemini", "ok", 6.016004, 250.615),
                    judged("xai", "ok", 14.389752, 197.683),
                ],
            }
        ],
    },
    JUDGED: {
        "run_id": JUDGED,
        "started_at": "2026-09-28T17:48:21Z",
        "spend": {"total_usd": 30.1202, "unpriced": []},
        "source": {"run_id": RESUMED, "judges": [{"repeat": 0, "run": ["openai"], "carried": ["anthropic", "gemini", "xai"]}]},
        "repeats": [
            {
                "index": 0,
                "exit_status": {"code": 0},
                "phases": [{"name": "scaffold", "status": "incomplete"}, {"name": "mvp", "status": "ok"}],
                "judgements": [
                    judged("anthropic", "ok", 7.415676, 165.29, carried=True),
                    judged("openai", "ok", 30.120172, 1894.477),
                    judged("gemini", "ok", 6.016004, 250.615, carried=True),
                    judged("xai", "ok", 14.389752, 197.683, carried=True),
                ],
            }
        ],
    },
}


def folders(root: Path, records: dict) -> dict[str, Path]:
    out = {}
    for name, data in records.items():
        (root / name).mkdir(parents=True)
        (root / name / "results.json").write_text(json.dumps(data), encoding="utf-8")
        out[name] = root / name
    return out


def test_a_run_its_resume_and_a_resume_of_its_judges_are_one_chain(tmp_path):
    made = folders(tmp_path / "create-full-system", RECORDS)
    assert CH.lineage(made[JUDGED]) == ([made[FIRST], made[RESUMED], made[JUDGED]], None)
    found = CH.of(made[JUDGED])
    assert [(f["run_id"], f["total_usd"]) for f in found["folders"]] == [(FIRST, 82.5281), (RESUMED, 87.7648), (JUDGED, 30.1202)]
    # Each stage once, by the folder that ran it: no carried phase, no carried judgement.
    assert [(s["stage"], s["run_id"], s["status"], s["cost_usd"], s["time_s"]) for s in found["stages"]] == [
        ("scaffold", FIRST, "incomplete", 82.528093, 9739.373),
        ("mvp", RESUMED, "ok", 22.631202, 3333.834),
        ("review", RESUMED, "ok", 20.350875, 630.849),
        ("close", RESUMED, "ok", 15.301141, 1145.569),
        ("judges", RESUMED, "missed", 29.481516, 250.615),  # agentic judges run at once: the longest
        ("judges", JUDGED, "ok", 30.120172, 1894.477),
    ]
    assert found["stages"][4]["judges"] == ["anthropic", "openai", "gemini", "xai"] and found["stages"][4]["missed"] == ["openai"]
    assert found["total_usd"] == 200.4131 and found["at_least"] is False and "broken" not in found
    assert CH.cost_text(found) == "$200.41"
    # The resume in between ends a chain of its own two folders.
    assert CH.of(made[RESUMED])["total_usd"] == 170.2929


def test_a_chain_breaks_on_a_source_not_beside_it_a_name_that_climbs_out_and_a_loop(tmp_path):
    made = folders(tmp_path / "one", {k: v for k, v in RECORDS.items() if k != FIRST})
    chain, broken = CH.lineage(made[JUDGED])
    assert chain == [made[RESUMED], made[JUDGED]]
    assert broken == f"{RESUMED} names {FIRST} as its source, and one holds no such run folder"
    found = CH.of(made[JUDGED])
    assert found["at_least"] is True and found["broken"] == broken and CH.cost_text(found) == "at least $117.89"
    climbs = dict(RECORDS[RESUMED], source={"run_id": "../elsewhere", "after": "scaffold"})
    (made[RESUMED] / "results.json").write_text(json.dumps(climbs), encoding="utf-8")
    assert CH.lineage(made[JUDGED])[1] == f"{RESUMED} names '../elsewhere' as its source, which is no run folder's name"
    loop = dict(RECORDS[RESUMED], source={"run_id": JUDGED, "after": "scaffold"})
    (made[RESUMED] / "results.json").write_text(json.dumps(loop), encoding="utf-8")
    assert CH.lineage(made[JUDGED]) == (
        [made[RESUMED], made[JUDGED]],
        f"{RESUMED} names {JUDGED} as its source, and {JUDGED} is later in its chain",
    )


def test_the_folders_that_ran_from_a_folder_are_those_whose_results_name_it_and_a_dry_run_is_none(tmp_path):
    made = folders(tmp_path / "one", RECORDS)
    dry = tmp_path / "one" / "20260929-000000-create-full-system-dry"
    dry.mkdir()
    (dry / "run.json").write_text(json.dumps({"source": {"run_id": FIRST}}), encoding="utf-8")  # a dry run
    assert CH.continued_by(made[FIRST]) == [made[RESUMED]] and CH.continued_by(made[JUDGED]) == []
    assert CH.newest(made[FIRST]) == made[JUDGED] and CH.newest(made[JUDGED]) == made[JUDGED]
    assert CH.fork(made[JUDGED]) is None
    assert CH.fork(made[FIRST]) == (
        f"{FIRST} is already the source of {RESUMED}, beside it. A chain has one line, so a run resumes or is judged "
        f"again from the chain's newest folder, {JUDGED}, which reaches every milestone before it"
    )


def test_a_folder_with_no_results_names_its_source_by_its_run_json(tmp_path):
    made = folders(tmp_path / "one", {FIRST: RECORDS[FIRST]})
    (tmp_path / "one" / RESUMED).mkdir()
    (tmp_path / "one" / RESUMED / "run.json").write_text(json.dumps({"source": {"run_id": FIRST}}), encoding="utf-8")
    assert CH.lineage(tmp_path / "one" / RESUMED) == ([made[FIRST], tmp_path / "one" / RESUMED], None)
    found = CH.of(tmp_path / "one" / RESUMED)
    assert found["folders"][1] == {"run_id": RESUMED, "started_at": None, "total_usd": None} and found["at_least"] is True


def test_a_subject_of_one_session_is_one_stage_and_one_shot_judges_take_their_sum():
    record = {
        "spend": {"total_usd": 0.3069, "unpriced": []},
        "repeats": [
            {
                "index": 0,
                "exit_status": {"code": 0, "duration_s": 41.5},
                "subject_cost_usd": 0.1,
                "judgements": [
                    {"provider": "anthropic", "status": "ok", "cost_usd": 0.1, "latency_s": 10.0, "verdict": {}},
                    {"provider": "openai", "status": "error", "cost_usd": None, "latency_s": 5.0, "verdict": None},
                ],
            },
            {"index": 1, "exit_status": {"code": 1, "duration_s": 3.0}, "subject_cost_usd": None, "judgements": []},
        ],
    }
    found = CH.chain([("one", record)])
    assert [(s["repeat"], s["stage"], s["status"], s["cost_usd"], s["time_s"]) for s in found["stages"]] == [
        (0, "subject", "ok", 0.1, 41.5),
        (0, "judges", "missed", 0.1, 15.0),  # one after another: the sum
        (1, "subject", "failed", None, 3.0),
    ]
    assert CH.cost_text(found) == "$0.31"


def test_a_phase_that_reported_no_cost_counts_the_harness_s_estimate_and_says_so():
    record = {"repeats": [{"index": 0, "exit_status": {"code": 1}, "phases": [phase("mvp", "failed", None, 0.79, None)]}]}
    (stage,) = CH.chain([("one", record)])["stages"]
    assert stage["cost_usd"] == 0.79 and stage["estimated"] is True and stage["time_s"] is None


def test_a_judge_again_ran_no_subject_and_judges_that_all_missed_failed():
    record = {
        "source": {"run_id": "x"},
        "repeats": [
            {
                "index": 0,
                "exit_status": {"code": 0},
                "phases": [{"name": "scaffold", "status": "ok"}],
                "judgements": [judged("anthropic", "error", 0.5, 3.0), judged("openai", "skipped", None, 0.0)],
            }
        ],
    }
    (stage,) = CH.chain([("again", record)])["stages"]
    assert stage["stage"] == "judges" and stage["status"] == "failed" and stage["missed"] == ["anthropic", "openai"]


@pytest.mark.parametrize(
    ("spent", "text"),
    [
        ([None, None], "—"),
        ([0.125, None], "at least $0.13"),  # halves round up, as a reader rounds
        ([1234.5, 0.004], "$1,234.50"),
    ],
)
def test_a_chain_s_cost_reads_as_its_row_s_cell(spent, text):
    records = [(str(i), {} if s is None else {"spend": {"total_usd": s, "unpriced": []}}) for i, s in enumerate(spent)]
    assert CH.cost_text(CH.chain(records)) == text


def test_a_model_with_no_price_makes_the_total_a_lower_bound():
    found = CH.chain([("one", {"spend": {"total_usd": 1.0, "unpriced": ["unknown-model"]}})])
    assert found["unpriced"] == ["unknown-model"] and found["at_least"] is True and CH.cost_text(found) == "at least $1.00"


def test_the_report_shows_each_folder_each_stage_and_the_total_and_says_where_a_chain_breaks(tmp_path):
    from harness import results as R

    made = folders(tmp_path / "one", {k: v for k, v in RECORDS.items() if k != FIRST})
    lines = R.chain_lines(CH.of(made[JUDGED]))
    assert lines[0] == "## Chain"
    assert f"| `{RESUMED}` | 2026-09-28T13:50:26Z | $87.7648 |" in lines
    assert f"| 0 | mvp | `{RESUMED}` | ok | $22.6312 | 0:55:34 |" in lines
    assert f"| 0 | judges (anthropic, openai, gemini, xai) | `{RESUMED}` | missed: openai | $29.4815 | 0:04:11 |" in lines
    assert "Total: at least $117.8850." in lines
    assert f"The chain breaks: {RESUMED} names {FIRST} as its source, and one holds no such run folder. " in "\n".join(lines)
