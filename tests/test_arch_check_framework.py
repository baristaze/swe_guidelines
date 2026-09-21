"""checkers/src/arch_check: config, exceptions, inline ignores, reports, exit codes.

The base tree is clean under every shipped rule, so a finding here is
one a test planted. The one the framework tests bend is CON-12: a file
under `acme.om` that imports `acme.services`. Counts and lists of rules
come from the registry, never from a number written down.
"""

import re
from pathlib import Path

import pytest

pytest.importorskip("tomllib")

from arch_check import __version__, registry
from arch_check.config import ConfigError, find_root, glob_match, load
from arch_check.lenses import LENSES
from arch_check.model import Violation
from arch_check.project import Project, parameters
from arch_check_fixtures import ADR, BASE, PYPROJECT, WORKSPACE, check, check_json, rules_found, write, write_project

REPO = Path(__file__).resolve().parent.parent
IMPL = "om/src/acme/om/tasks/impl.py"
BAD = "from acme.services.api import app\n"
SHIPPED = [r.id for r in registry.rules()]
PY_FILES = sum(1 for rel in BASE if rel.endswith(".py") and "/src/" in rel)
FREE = next(id for id in sorted(LENSES) if id not in SHIPPED)
"""A lens no shipped rule decides, for a local rule to take."""


def bad_project(tmp_path, pyproject=PYPROJECT, **files):
    return write_project(tmp_path, {IMPL: BAD, **files}, pyproject=pyproject)


# --- exit codes and reports


def test_clean_tree_exits_0(tmp_path):
    write_project(tmp_path)
    code, out, err = check(tmp_path)
    assert (code, err) == (0, "")
    assert out.startswith(f"arch-check ok: {len(SHIPPED)} rule(s) over {PY_FILES} Python file(s)")


def test_the_base_tree_is_clean_under_every_rule(tmp_path):
    write_project(tmp_path)
    code, report = check_json(tmp_path)
    assert (code, report["findings"], report["exceptions_applied"]) == (0, [], [])
    assert [r["id"] for r in report["rules_run"]] == SHIPPED
    assert {r["group"] for r in report["rules_run"]} == {r.group for r in registry.rules()}


def test_a_finding_exits_1_with_a_text_line(tmp_path):
    bad_project(tmp_path)
    code, out, _ = check(tmp_path)
    assert code == 1
    assert out.splitlines()[0].startswith(f"{IMPL}:1:1: CON-12 acme.om.tasks.impl imports acme.services.api;")
    assert f"1 finding(s) from {len(SHIPPED)} rule(s)" in out


def test_json_report_shape(tmp_path):
    bad_project(tmp_path)
    code, report = check_json(tmp_path)
    assert code == 1
    assert list(report) == ["version", "root", "rules_run", "findings", "exceptions_applied"]
    assert report["version"] == __version__
    assert report["root"] == str(tmp_path.resolve())
    assert [r["id"] for r in report["rules_run"]] == SHIPPED
    assert next(r for r in report["rules_run"] if r["id"] == "CON-10") == {
        "id": "CON-10",
        "lens": "CON-10",
        "group": "contracts",
        "coverage": "partial",
        "severity": "medium",
        "summary": registry.RULES["CON-10"].summary,
        "origin": "guideline",
    }
    finding = report["findings"][0]
    assert list(finding) == ["rule", "group", "severity", "path", "line", "col", "message", "origin"]
    assert (finding["rule"], finding["group"], finding["severity"], finding["path"]) == ("CON-12", "contracts", "medium", IMPL)
    assert report["exceptions_applied"] == []


def test_a_file_that_does_not_parse_is_a_parse_finding(tmp_path):
    write_project(tmp_path, {"om/src/acme/om/broken.py": "x = 1\ndef f(:\n"})
    code, report = check_json(tmp_path)
    assert code == 1
    assert rules_found(report) == [("PARSE", "om/src/acme/om/broken.py", 2)]
    assert report["findings"][0]["group"] == "framework"
    assert report["findings"][0]["message"].startswith("does not parse:")


def test_list_prints_every_rule_and_needs_no_project(tmp_path):
    code, out, _ = check(tmp_path, "--list")
    assert code == 0
    lines = out.splitlines()
    assert [line.split()[0] for line in lines] == SHIPPED
    assert lines[SHIPPED.index("CON-12")].split()[1:5] == ["contracts", "partial", "medium", "guideline"]


def test_group_and_rule_select_what_runs(tmp_path):
    bad_project(tmp_path)
    _, report = check_json(tmp_path, "--rule", "CON-10")
    assert [r["id"] for r in report["rules_run"]] == ["CON-10"]
    assert report["findings"] == []
    code, report = check_json(tmp_path, "--group", "om")
    om = [r.id for r in registry.rules() if r.group == "om"]
    assert (code, [r["id"] for r in report["rules_run"]]) == (0, om)
    assert om


@pytest.mark.parametrize(
    "args,message",
    [
        (("--group", "nope"), "unknown group(s) nope"),
        (("--rule", "CON-99"), "unknown rule(s) CON-99"),
    ],
)
def test_an_unknown_selection_exits_2(tmp_path, args, message):
    write_project(tmp_path)
    code, _, err = check(tmp_path, *args)
    assert code == 2
    assert message in err


def test_an_unknown_flag_or_format_exits_2(tmp_path):
    write_project(tmp_path)
    for args in (("--format", "xml"), ("--chekc",)):
        with pytest.raises(SystemExit) as exit_:
            check(tmp_path, *args)
        assert exit_.value.code == 2


def test_paths_limit_what_is_reported(tmp_path):
    bad_project(tmp_path)
    code, _, _ = check(tmp_path, str(tmp_path / "infra"))
    assert code == 0
    code, _, _ = check(tmp_path, str(tmp_path / "om" / "src"))
    assert code == 1


def test_a_path_outside_the_root_exits_2(tmp_path):
    write_project(tmp_path / "repo")
    code, _, err = check(tmp_path / "repo", str(tmp_path))
    assert code == 2
    assert "is outside the root" in err


# --- configuration


def test_no_config_infers_the_package_from_om_src(tmp_path):
    bad_project(tmp_path, pyproject=WORKSPACE + '[project]\nname = "acme"\n')
    code, report = check_json(tmp_path)
    assert code == 1
    assert rules_found(report) == [("CON-12", IMPL, 1)]


def test_no_pyproject_at_all_still_runs(tmp_path):
    write_project(tmp_path, pyproject=None)
    code, report = check_json(tmp_path)
    # Every rule runs; the one finding is the workspace a root pyproject.toml would declare.
    assert [r["id"] for r in report["rules_run"]] == SHIPPED
    assert (code, rules_found(report)) == (1, [("DEL-11", "pyproject.toml", 1)])


def test_no_config_and_no_single_package_exits_2(tmp_path):
    write_project(tmp_path, {"om/src/other/om/__init__.py": ""}, pyproject=WORKSPACE)
    code, _, err = check(tmp_path)
    assert code == 2
    assert "pass --package" in err
    code, report = check_json(tmp_path, "--package", "acme")
    # It runs; the stray package is a second root package under om/src.
    assert (code, rules_found(report)) == (1, [("DEL-08", "om/pyproject.toml", 1)])


def test_bad_toml_exits_2(tmp_path):
    write_project(tmp_path, pyproject="[tool.arch-check\n")
    code, _, err = check(tmp_path)
    assert code == 2
    assert "pyproject.toml" in err


@pytest.mark.parametrize(
    "extra,message",
    [
        ('pakage = "acme"\n', "unknown key(s) pakage"),
        ('src = "om/src"\n', "`src` is not a list of strings"),
        ('disable = ["CON-12"]\n', "'CON-12' names no ADR"),
    ],
)
def test_a_malformed_config_exits_2(tmp_path, extra, message):
    write_project(tmp_path, pyproject=PYPROJECT + extra)
    code, _, err = check(tmp_path)
    assert code == 2
    assert message in err


def test_the_package_must_be_a_python_name(tmp_path):
    write_project(tmp_path)
    code, _, err = check(tmp_path, "--package", "acme-x")
    assert code == 2
    assert "is not a dotted Python name" in err


def test_src_and_exclude_are_configurable(tmp_path):
    bad_project(tmp_path, pyproject=PYPROJECT + 'exclude = ["**/tasks/**"]\n')
    assert check(tmp_path)[0] == 0
    bad_project(tmp_path, pyproject=PYPROJECT + 'src = ["infra/src"]\n')
    assert check(tmp_path)[0] == 0


def test_find_root_walks_up_to_the_config(tmp_path):
    write_project(tmp_path)
    assert find_root(tmp_path / "om" / "src" / "acme") == tmp_path.resolve()
    assert find_root(tmp_path.parent) == tmp_path.parent.resolve()


def test_glob_match():
    assert glob_match("om/src/**/impl/*.py", "om/src/acme/om/storage/impl/legacy.py")
    assert glob_match("**/migrations/**", "om/src/acme/om/migrations/v1/a.py")
    assert glob_match("services/*/src", "services/api/src")
    assert not glob_match("services/*/src", "services/api/x/src")
    assert not glob_match("om/*.py", "om/src/a.py")


# --- disables and exceptions: each needs an ADR that exists


DISABLE = '[[tool.arch-check.disable]]\nrule = "CON-12"\nadr = "{adr}"\nreason = "a worker hook"\n'
EXCEPTION = '[[tool.arch-check.exception]]\nrule = "CON-12"\npath = "{path}"\nadr = "{adr}"\nreason = "legacy"\n'


def test_a_disabled_rule_does_not_run(tmp_path):
    bad_project(tmp_path, pyproject=PYPROJECT + DISABLE.format(adr=ADR))
    code, report = check_json(tmp_path)
    assert code == 0
    assert [r["id"] for r in report["rules_run"]] == [id for id in SHIPPED if id != "CON-12"]


def test_a_disable_without_an_existing_adr_exits_2(tmp_path):
    bad_project(tmp_path, pyproject=PYPROJECT + DISABLE.format(adr="docs/adr/0099-missing.md"))
    code, _, err = check(tmp_path)
    assert code == 2
    assert "ADR file docs/adr/0099-missing.md does not exist" in err


def test_a_disable_of_an_unknown_rule_exits_2(tmp_path):
    bad_project(tmp_path, pyproject=PYPROJECT + DISABLE.format(adr=ADR).replace("CON-12", "CON-99"))
    code, _, err = check(tmp_path)
    assert code == 2
    assert "unknown rule CON-99" in err


def test_an_exception_accepts_the_findings_its_path_matches(tmp_path):
    bad_project(tmp_path, pyproject=PYPROJECT + EXCEPTION.format(path="om/src/**/tasks/*.py", adr=ADR))
    code, report = check_json(tmp_path)
    assert code == 0
    assert report["exceptions_applied"] == [
        {"rule": "CON-12", "path": IMPL, "line": 1, "adr": ADR, "source": "config", "reason": "legacy"}
    ]


def test_an_exception_without_an_existing_adr_exits_2(tmp_path):
    bad_project(tmp_path, pyproject=PYPROJECT + EXCEPTION.format(path=IMPL, adr="docs/adr/0002-x.md"))
    assert check(tmp_path)[0] == 2


def test_an_exception_without_a_path_exits_2(tmp_path):
    bad_project(tmp_path, pyproject=PYPROJECT + EXCEPTION.format(path=IMPL, adr=ADR).replace(f'path = "{IMPL}"\n', ""))
    code, _, err = check(tmp_path)
    assert code == 2
    assert "`path` is missing" in err


def test_an_exception_that_matches_nothing_is_an_ignore_finding(tmp_path):
    write_project(tmp_path, pyproject=PYPROJECT + EXCEPTION.format(path=IMPL, adr=ADR))
    code, report = check_json(tmp_path)
    assert code == 1
    assert rules_found(report) == [("IGNORE", "pyproject.toml", 1)]
    assert "matches no finding" in report["findings"][0]["message"]


# --- inline ignores: `# arch-check: ignore[RULE] ADR-NNNN`


def test_an_inline_ignore_with_an_existing_adr_accepts_the_finding(tmp_path):
    bad_project(tmp_path, **{IMPL: "from acme.services.api import app  # arch-check: ignore[CON-12] ADR-0001\n"})
    code, report = check_json(tmp_path)
    assert code == 0
    assert report["exceptions_applied"][0]["source"] == "inline"
    assert report["exceptions_applied"][0]["adr"] == ADR


def test_an_inline_ignore_covers_its_own_line_only(tmp_path):
    bad_project(tmp_path, **{IMPL: "# arch-check: ignore[CON-12] ADR-0001\nfrom acme.services.api import app\n"})
    code, report = check_json(tmp_path)
    assert code == 1
    assert rules_found(report) == [("IGNORE", IMPL, 1), ("CON-12", IMPL, 2)]


@pytest.mark.parametrize(
    "comment,message,also",
    [
        # DEL-23 holds every ADR number cited in code to a file, the comment's included.
        ("# arch-check: ignore[CON-12] ADR-0042", "cites ADR-0042, and docs/adr has no 0042-*.md", [("DEL-23", IMPL, 1)]),
        ("# arch-check: ignore[CON-12]", "cites no ADR", []),
        ("# arch-check: ignore[CON-99] ADR-0001", "unknown rule(s) CON-99", []),
        ("# arch-check: ignore[] ADR-0001", "names no rule", []),
    ],
)
def test_a_malformed_inline_ignore_is_an_ignore_finding_and_accepts_nothing(tmp_path, comment, message, also):
    bad_project(tmp_path, **{IMPL: f"from acme.services.api import app  {comment}\n"})
    code, report = check_json(tmp_path)
    assert code == 1
    assert sorted(rules_found(report)) == sorted([("CON-12", IMPL, 1), ("IGNORE", IMPL, 1), *also])
    ignore = next(f for f in report["findings"] if f["rule"] == "IGNORE")
    assert message in ignore["message"]


def test_an_inline_ignore_of_a_rule_that_reports_nothing_there_is_an_ignore_finding(tmp_path):
    write_project(tmp_path, {IMPL: "import os  # arch-check: ignore[CON-12] ADR-0001\n"})
    code, report = check_json(tmp_path)
    assert code == 1
    assert "ignores CON-12, which reports nothing here" in report["findings"][0]["message"]
    # Not judged when the rule did not run.
    assert check(tmp_path, "--rule", "CON-10")[0] == 0


# --- the model and the project helpers


def test_a_registered_rule_runs_through_the_cli(tmp_path, monkeypatch):
    def everything_is_wrong(project):
        for f in project.python_files:
            yield Violation(f.rel, 1, 1, "wrong")

    rule = registry.make("OM-03", everything_is_wrong, coverage="full", summary="everything is wrong")
    monkeypatch.setitem(registry.RULES, "OM-03", rule)
    write_project(tmp_path)
    code, report = check_json(tmp_path, "--rule", "OM-03")
    assert code == 1
    assert len(report["findings"]) == PY_FILES
    assert report["findings"][0]["severity"] == LENSES["OM-03"]


@pytest.mark.parametrize(
    "id,extra,message",
    [
        ("OM-1", {}, "is not PREFIX-NN"),
        ("XX-01", {}, "no lens group has the prefix XX"),
        ("OM-99", {}, "no lens has this id"),
        ("CON-12", {"group": "om"}, "the lens is in 'contracts'"),
        ("CON-12", {"severity": "high"}, "the lens says 'medium'"),
        ("CON-12", {"coverage": "most"}, "coverage 'most'"),
    ],
)
def test_a_rule_must_match_the_lens_catalog(id, extra, message):
    kwargs = {"coverage": "full", "summary": "s", **extra}
    with pytest.raises(registry.RegistryError, match=message):
        registry.make(id, lambda p: [], **kwargs)


def test_a_shipped_id_registers_once():
    registry.load()
    with pytest.raises(registry.RegistryError, match="registered twice"):
        registry.register(registry.make("CON-12", lambda p: [], coverage="full", summary="s"))


def test_the_lens_table_matches_the_lens_files():
    """`arch_check/lenses.py` carries every lens id and severity of `lenses/*.md`."""
    found: dict[str, str] = {}
    for path in sorted((REPO / "lenses").glob("*.md")):
        if path.name == "README.md":
            continue
        lens = None
        fenced = False
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("```"):
                fenced = not fenced
            elif not fenced and (m := re.match(r"^## ([A-Z]{2,3}-\d{2}) ", line)):
                lens = m.group(1)
            elif not fenced and lens and (m := re.match(r"^\*\*Severity\.\*\*\s*(\w+)", line)):
                found[lens] = m.group(1)
                lens = None
    missing = {k: v for k, v in found.items() if LENSES.get(k) != v}
    extra = sorted(set(LENSES) - set(found))
    assert (missing, extra) == ({}, []), "update checkers/src/arch_check/lenses.py to match lenses/*.md"


def test_module_names_follow_the_src_roots(tmp_path):
    write_project(tmp_path)
    project = Project(load(tmp_path))
    modules = [f.module for f in project.python_files]
    assert "acme.om.tasks" in modules
    assert "acme.services.api" in modules
    assert "acme.workers.maintenance" in modules
    assert project.module("acme.om.tasks.impl") is not None
    [impl] = project.modules_under("acme.om.tasks.impl")
    assert [i.module for i in project.imports(impl)] == ["acme.om.root"]
    assert project.import_graph("acme.workers")["acme.workers.maintenance"] == {"acme.om", "acme.om.tasks"}


def test_signature_helpers(tmp_path):
    write(tmp_path, "om/src/acme/om/sig.py", "def f(self, ctx: Ctx, /, a: int = 1, *rest, key: str, **kw) -> None: ...\n")
    write_project(tmp_path)
    project = Project(load(tmp_path))
    [(_, tree)] = list(project.trees("acme.om.sig"))
    fn = tree.body[0]
    assert isinstance(fn, __import__("ast").FunctionDef)
    params = parameters(fn)
    assert [(p.name, p.annotation, p.kind, p.has_default) for p in params] == [
        ("self", None, "positional", False),
        ("ctx", "Ctx", "positional", False),
        ("a", "int", "positional", True),
        ("rest", None, "vararg", False),
        ("key", "str", "keyword", False),
        ("kw", None, "varkw", False),
    ]


def test_load_raises_config_error_for_a_missing_root(tmp_path):
    with pytest.raises(ConfigError, match="is not a directory"):
        load(tmp_path / "missing")


# --- project-local rules: `local = [...]` in the config

LOCAL = PYPROJECT + 'local = ["tools/arch_check"]\n'
LOCAL_RULE = """\
from arch_check.model import Violation
from arch_check.registry import rule


@rule("{id}", coverage="{coverage}", summary="no module is named legacy")
def no_legacy(project):
    for file in project.python_files:
        if file.module.endswith(".legacy"):
            yield Violation(file.rel, 1, 1, f"{{file.module}} is named legacy")
"""


def local_project(tmp_path, id=FREE, coverage="partial", **files):
    rules = {"tools/arch_check/naming.py": LOCAL_RULE.format(id=id, coverage=coverage)}
    return write_project(tmp_path, {**rules, **files}, pyproject=LOCAL)


def test_a_local_rule_runs_and_is_marked_local(tmp_path):
    local_project(tmp_path, **{"om/src/acme/om/legacy.py": ""})
    code, report = check_json(tmp_path)
    assert code == 1
    assert {(r["id"], r["origin"]) for r in report["rules_run"]} == {(id, "guideline") for id in SHIPPED} | {(FREE, "local")}
    [finding] = report["findings"]
    assert (finding["rule"], finding["origin"], finding["severity"]) == (FREE, "local", LENSES[FREE])


def test_list_shows_local_rules(tmp_path):
    local_project(tmp_path)
    code, out, _ = check(tmp_path, "--list")
    assert code == 0
    [local] = [line.split()[:5] for line in out.splitlines() if line.split()[4] == "local"]
    assert local == [FREE, registry.group_of(FREE), "partial", LENSES[FREE], "local"]


def test_a_local_rule_may_add_to_a_lens_a_shipped_rule_decides_in_part(tmp_path):
    local_project(tmp_path, id="CON-12", **{"om/src/acme/om/legacy.py": ""})
    code, report = check_json(tmp_path, "--rule", "CON-12")
    assert code == 1
    assert [(r["id"], r["origin"]) for r in report["rules_run"]] == [("CON-12", "guideline"), ("CON-12", "local")]


def test_a_local_rule_cannot_take_a_lens_a_shipped_rule_decides_whole(tmp_path, monkeypatch):
    whole = registry.make(FREE, lambda p: [], coverage="full", summary="shipped")
    monkeypatch.setitem(registry.RULES, FREE, whole)
    local_project(tmp_path)
    code, _, err = check(tmp_path)
    assert code == 2
    assert f"tools/arch_check/naming.py: {FREE} is decided whole by the shipped rule" in err


@pytest.mark.parametrize(
    "id,coverage,message",
    [("OM-99", "partial", "no lens has this id"), (FREE, "most", "coverage 'most'")],
)
def test_a_local_rule_must_name_a_lens_and_a_coverage(tmp_path, id, coverage, message):
    local_project(tmp_path, id=id, coverage=coverage)
    code, _, err = check(tmp_path)
    assert code == 2
    assert "tools/arch_check/naming.py" in err
    assert message in err


def test_a_local_file_that_fails_to_import_exits_2_with_its_path(tmp_path):
    local_project(tmp_path, **{"tools/arch_check/broken.py": "import no_such_module_anywhere\n"})
    code, _, err = check(tmp_path)
    assert code == 2
    assert "tools/arch_check/broken.py: fails to import: ModuleNotFoundError" in err


def test_a_missing_local_directory_exits_2(tmp_path):
    write_project(tmp_path, pyproject=LOCAL)
    code, _, err = check(tmp_path)
    assert code == 2
    assert "local rule directory tools/arch_check does not exist" in err


def test_local_rules_do_not_leak_into_the_next_run(tmp_path):
    local_project(tmp_path / "one")
    write_project(tmp_path / "two")
    assert FREE in check(tmp_path / "one", "--list")[1]
    assert FREE not in check(tmp_path / "two", "--list")[1]
    assert FREE not in registry.RULES


def test_rule_options_are_read_and_checked(tmp_path):
    write_project(tmp_path, pyproject=PYPROJECT + '\n[tool.arch-check.options.CTX-26]\nsites = ["a.py"]\n')
    project = Project(load(tmp_path))
    assert project.option("CTX-26", "sites", [], {"sites"}) == ["a.py"]
    assert project.option("CTX-10", "names", ["x"], {"names"}) == ["x"]
    with pytest.raises(ConfigError, match="unknown key"):
        project.option("CTX-26", "site", [], {"site"})
    with pytest.raises(ConfigError, match="must be a int"):
        project.option("CTX-26", "sites", 0, {"sites"})


def test_options_for_an_unknown_rule_is_a_config_error(tmp_path):
    write_project(tmp_path, pyproject=PYPROJECT + "\n[tool.arch-check.options.XYZ-01]\nsites = []\n")
    code, _, err = check(tmp_path)
    assert code == 2
    assert "unknown rule XYZ-01" in err
