"""checkers/src/arch_check: a file the parser or a rule cannot hold, and a rule that raises.

A source file that exhausts the parser, or nests deeper than the rules
walk, is a `PARSE` finding on that file. A rule that raises is an
`ERROR` finding naming the rule: the other rules still run, their
findings are still reported, and the run exits 2.
"""

import ast

import pytest

pytest.importorskip("tomllib")

from arch_check import registry
from arch_check.model import Violation
from arch_check.project import MAX_DEPTH
from arch_check_fixtures import check, check_json, rules_found, write_project

IMPL = "om/src/acme/om/tasks/impl/manager.py"
BAD = "from acme.services.api import app\n"
DEEP = "om/src/acme/om/deep.py"


def test_a_file_that_exhausts_the_parser_is_a_parse_finding(tmp_path):
    write_project(tmp_path, {DEEP: "x = " + "-" * 200_000 + "1\n"})
    code, report = check_json(tmp_path)
    assert code == 1
    assert rules_found(report) == [("PARSE", DEEP, 1)]


def test_a_file_nested_deeper_than_the_rules_walk_is_a_parse_finding(tmp_path):
    write_project(tmp_path, {DEEP: "x = 1" + " + 1" * (MAX_DEPTH + 10) + "\n"})
    code, report = check_json(tmp_path)
    assert code == 1
    assert rules_found(report) == [("PARSE", DEEP, 1)]
    assert "nests deeper than" in report["findings"][0]["message"]


def test_a_rule_walks_a_file_nested_just_under_the_limit(tmp_path, monkeypatch):
    def walks(project):
        for f, tree in project.trees():
            ast.unparse(tree)
            ast.NodeVisitor().visit(tree)
            yield from ()

    monkeypatch.setitem(registry.RULES, "OM-03", registry.make("OM-03", walks, coverage="full", summary="walks"))
    write_project(tmp_path, {DEEP: "x = 1" + " + 1" * (MAX_DEPTH - 50) + "\n"})
    code, out, err = check(tmp_path, "--rule", "OM-03")
    assert (code, err) == (0, ""), out


def test_a_rule_that_raises_is_an_error_and_the_others_still_run(tmp_path, monkeypatch):
    def raises(project):
        yield Violation(IMPL, 1, 1, "found before the raise")
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setitem(registry.RULES, "OM-03", registry.make("OM-03", raises, coverage="full", summary="raises"))
    write_project(tmp_path, {IMPL: BAD})
    code, report = check_json(tmp_path)
    assert code == 2
    found = rules_found(report)
    assert ("CON-12", IMPL, 1) in found
    errors = [f for f in report["findings"] if f["rule"] == "ERROR"]
    assert len(errors) == 1
    assert errors[0]["group"] == "framework"
    assert errors[0]["message"].startswith("OM-03 raised RecursionError")
    # what the rule found before it raised is not reported as if the rule had finished
    assert not any(f["rule"] == "OM-03" for f in report["findings"])


def test_an_error_is_reported_whatever_paths_are_asked_for(tmp_path, monkeypatch):
    def raises(project):
        raise MemoryError
        yield

    monkeypatch.setitem(registry.RULES, "OM-03", registry.make("OM-03", raises, coverage="full", summary="raises"))
    write_project(tmp_path)
    code, out, err = check(tmp_path, str(tmp_path / "om"))
    assert code == 2
    assert "ERROR OM-03 raised MemoryError" in out
    assert "MemoryError" in err
