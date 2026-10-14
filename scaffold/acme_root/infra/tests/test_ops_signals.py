"""The ops skills that read signals read one file per namespace or process.

What an investigation meets of one namespace (its kinds, its outcomes,
its gauges and alarms, its log lines, its failure events) sits in
`.agents/skills/_shared/signals/<ns>.md`. The skills keep their spine and
name no namespace, so a copy that adds a namespace adds its file and edits
no skill, and the scaffold's next release edits the scaffold's files
alone. What moved into a signals file is held to the code it names: a
gauge, an alarm, and a failure event are each the code's, in its words.
"""

import re
from pathlib import Path

import pytest
from skills_support import (
    OPERATOR_READ,
    ROOT,
    SIGNALS,
    dashboard_schemas,
    prose_of,
    search_schemas,
    text_of,
)

# The skills that read every signals file.
READERS = ["ops-investigate", "ops-root-cause", "ops-watch"]
README = SIGNALS / "README.md"
FILES = sorted(path for path in SIGNALS.glob("*.md") if path != README)
HEADINGS = re.findall(r"^- `## (.+?)`", README.read_text(), re.MULTILINE)
CODE_SPAN = re.compile(r"`([^`]+)`")
# The headings whose code spans name something the code or the deployment
# declares, which the spine of a skill never writes.
DECLARED = ("Kinds", "Gauges", "Alarms", "Queues", "Change events")


def _section(path: Path, heading: str) -> str:
    """A signals file's text under one `## ` heading, on one line."""
    parts = re.split(r"^## (.+)$", path.read_text(), flags=re.MULTILINE)
    held = dict(zip(parts[1::2], parts[2::2], strict=True))
    return " ".join(held.get(heading, "").split())


def _spans(heading: str, files: list[Path] = FILES) -> set[str]:
    """Every code span under one heading, across the files."""
    return {span for path in files for span in CODE_SPAN.findall(_section(path, heading))}


def _sources() -> list[Path]:
    """The Python of every distribution's `src/`, where a gauge or an event is declared."""
    roots = [*ROOT.glob("*/src"), *ROOT.glob("*/*/src")]
    return sorted(path for root in roots for path in root.rglob("*.py"))


@pytest.mark.parametrize("name", READERS)
def test_a_skill_that_reads_signals_reads_every_file_of_the_folder(name: str) -> None:
    """The step is the skill's own, and the folder is a path from its folder;
    a copy's namespace is never named in it."""
    text = prose_of(name)
    assert "read every file in `../_shared/signals/`" in text
    assert "a copy adds `<ns>.md` there for each namespace of its own" in text


def test_the_watch_hands_its_sub_agent_the_signals_from_the_repository_root() -> None:
    """A sub-agent gets the skill's text without its folder, so it reads the
    signals by their path from the root, as it reads the preamble."""
    root_path = f"{SIGNALS.relative_to(ROOT).as_posix()}/"
    assert root_path == ".agents/skills/_shared/signals/"
    assert f"`{root_path}`" in prose_of("ops-watch")


def test_a_signals_file_is_named_by_a_namespace_or_a_process() -> None:
    namespaces = {path.parent.name for path in ROOT.glob("om/src/*/om/*/README.md")}
    processes = {
        path.name
        for folder in ("services", "workers", "apps")
        for path in (ROOT / folder).iterdir()
        if path.is_dir()
    }
    assert FILES, "the signals folder holds no namespace's file"
    unknown = {path.stem for path in FILES} - namespaces - processes
    assert not unknown, f"{sorted(unknown)} is neither a namespace nor a process"


@pytest.mark.parametrize("path", FILES, ids=lambda path: path.stem)
def test_a_signals_file_holds_the_readmes_headings_in_their_order(path: Path) -> None:
    """Each skill's step names the headings it reads, so a file under another
    heading says what no step reads. A namespace that emits nothing an
    investigation meets yet holds its title and its sentence alone."""
    assert HEADINGS, "the signals README lists no heading, or this test no longer reads it"
    lines = path.read_text().splitlines()
    assert lines[0] == f"# Signals: {path.stem}"
    held = [line.removeprefix("## ") for line in lines if line.startswith("## ")]
    assert set(held) <= set(HEADINGS), sorted(set(held) - set(HEADINGS))
    assert held == sorted(set(held), key=HEADINGS.index), held


@pytest.mark.parametrize("name", READERS)
def test_the_spine_writes_no_kind_gauge_alarm_queue_or_event_of_a_namespace(name: str) -> None:
    """A copy writes its namespace's into its own file, and the scaffold's
    release edits the scaffold's: no sentence of the skill is both's. An
    identifier is a span with a dot, an underscore, or a hyphen in it, or one
    in capitals."""
    spans = set().union(*(_spans(heading) for heading in DECLARED))
    identifiers = {span for span in spans if re.search(r"[._-]", span) or span.isupper()}
    assert identifiers, "the signals files declare nothing, or this test no longer reads them"
    written = sorted(term for term in identifiers if term in text_of(name))
    assert not written, f"{name} writes {written}, which a signals file holds"


def test_every_failure_event_a_namespace_records_is_its_signals_files() -> None:
    """The root cause picks a failing request by the failure events its
    signals files name: one the code records and no file names is a failure
    no run follows, and one a file names and no code records is never met."""
    recorded = {
        (path.relative_to(ROOT).parts[4], kind)
        for path in _sources()
        if path.relative_to(ROOT).parts[:2] == ("om", "src")
        for kind in re.findall(r'^DEAD_LETTER_KIND = "([a-z_.]+)"$', path.read_text(), re.M)
    }
    named = {
        (path.stem, kind)
        for path in FILES
        for kind in _spans("Change events", [path])
        if re.fullmatch(r"[a-z_]+(?:\.[a-z_]+)+", kind)
    }
    assert recorded, "no namespace records a failure event, or this test no longer sees one"
    assert recorded == named


def test_every_gauge_the_code_declares_is_a_signals_files_and_no_other() -> None:
    """A gauge is a sweep's read, which an investigation reads by the names
    its signals files give; a name no code declares reads nothing, and says
    so with no error."""
    declared = {
        gauge
        for path in _sources()
        for gauge in re.findall(r'Gauge\(\s*"([a-z0-9_]+)"', path.read_text())
    }
    assert declared, "no gauge is declared, or this test no longer sees one"
    named = {span for span in _spans("Gauges") if re.fullmatch(r"[a-z][a-z0-9_]*", span)}
    assert named == declared


def test_every_alarm_a_signals_file_names_is_declared_in_terraform() -> None:
    named = {
        suffix
        for span in _spans("Alarms")
        for suffix in re.findall(r"^acme-<env>-([a-z0-9-]+)$", span)
    }
    terraform = "\n".join(
        path.read_text() for path in (ROOT / "deployment" / "terraform").rglob("*.tf")
    )
    assert named, "no signals file names an alarm, or this test no longer sees one"
    missing = sorted(suffix for suffix in named if f'-{suffix}"' not in terraform)
    assert not missing, f"no Terraform alarm is named acme-<env>-{missing}"


def test_a_signals_file_holds_to_what_a_skills_commands_do() -> None:
    """A skill runs what its signals files say, so a read of the operator
    plane there goes through `jq`, and a `SEARCH` there is the dashboard's."""
    for path in [README, *FILES]:
        text = path.read_text()
        for read in OPERATOR_READ.finditer(text):
            assert read["piped"], f"{path.name} prints a read of the operator plane whole"
        unknown = search_schemas(text) - dashboard_schemas()
        assert not unknown, f"{path.name} searches {sorted(unknown)}, which the dashboard lacks"
