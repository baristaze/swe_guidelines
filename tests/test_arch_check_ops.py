"""checkers/src/arch_check/rules/ops.py: the skill set, the traffic tool, the READMEs, and the knowledge map."""

import pytest

pytest.importorskip("tomllib")

from arch_check.rules.ops import SKILLS
from arch_check_fixtures import check_json, rules_found, write_project


def found(tmp_path, rule, files):
    write_project(tmp_path, files)
    code, report = check_json(tmp_path, "--rule", rule)
    return code, rules_found(report)


def skills(*missing):
    return {f".claude/skills/{n}/SKILL.md": f"---\nname: {n}\n---\n" for n in SKILLS if n not in missing}


# --- OPS-11


def test_ops_11_the_nine_skills_pass(tmp_path):
    assert found(tmp_path, "OPS-11", skills()) == (0, [])


def test_ops_11_a_skill_with_no_frontmatter_passes(tmp_path):
    assert found(tmp_path, "OPS-11", {".claude/skills/stress-test-run/SKILL.md": "# Stress test\n"}) == (0, [])


def test_ops_11_a_missing_skill_fails(tmp_path):
    code, where = found(tmp_path, "OPS-11", {".claude/skills/ops-watch/SKILL.md": None})
    assert (code, where) == (1, [("OPS-11", ".claude/skills/ops-watch/SKILL.md", 1)])


# --- OPS-20


def test_ops_20_one_generator_passes(tmp_path):
    files = {"ops/pyproject.toml": '[project]\nname = "acme-ops"\ndependencies = ["httpx>=0.28"]\n'}
    assert found(tmp_path, "OPS-20", files) == (0, [])


def test_ops_20_one_generator_built_on_a_load_tool_passes(tmp_path):
    files = {"ops/pyproject.toml": '[project]\nname = "acme-ops"\ndependencies = ["locust>=2"]\n'}
    assert found(tmp_path, "OPS-20", files) == (0, [])


def test_ops_20_a_second_load_tool_fails(tmp_path):
    files = {
        "ops/pyproject.toml": '[project]\nname = "acme-ops"\n\n[dependency-groups]\nload = ["Locust>=2"]\n',
        "apps/portal/package.json": '{"devDependencies": {"k6": "0.1"}}',
    }
    code, where = found(tmp_path, "OPS-20", files)
    assert (code, where) == (1, [("OPS-20", "apps/portal/package.json", 1)])


# --- OPS-24

READMES = {f"{d}/README.md": "# x\n" for d in ("om", "services/api", "workers/maintenance", "deployment", "ops", "apps/portal")}


def test_ops_24_a_readme_at_every_level_passes(tmp_path):
    assert found(tmp_path, "OPS-24", READMES) == (0, [])


def test_ops_24_infra_and_a_client_are_not_levels(tmp_path):
    files = {**READMES, "infra/README.md": None, "clients/python/pyproject.toml": ""}
    assert found(tmp_path, "OPS-24", files) == (0, [])


def test_ops_24_a_level_without_a_readme_fails(tmp_path):
    files = {**READMES, "apps/cli/pyproject.toml": ""}
    assert found(tmp_path, "OPS-24", files) == (1, [("OPS-24", "apps/cli/README.md", 1)])


# --- OPS-25

NS = "om/src/acme/om/tasks/README.md"


def test_ops_25_namespace_readmes_and_a_plain_om_readme_pass(tmp_path):
    files = {
        NS: "# Tasks\n",
        "om/src/acme/om/storage/__init__.py": "",
        "om/README.md": "# Acme\n\nAn org has members;\nplace an order.\n",
    }
    assert found(tmp_path, "OPS-25", files) == (0, [])


def test_ops_25_a_namespace_without_a_readme_and_commands_in_om_readme_fail(tmp_path):
    files = {NS: None, "om/README.md": "# Acme\n\n```bash\nmake test\n```\n\nRun `uv run pytest` first.\n"}
    code, where = found(tmp_path, "OPS-25", files)
    assert code == 1
    assert where == [
        ("OPS-25", "om/README.md", 3),
        ("OPS-25", "om/README.md", 7),
        ("OPS-25", NS, 1),
    ]


def test_ops_25_a_diagram_of_the_nouns_passes(tmp_path):
    readme = "# Acme\n\n```mermaid\nerDiagram\n    ORDER ||--|{ ORDER_LINE : holds\n```\n"
    assert found(tmp_path, "OPS-25", {"om/README.md": readme}) == (0, [])


# --- OPS-26

MAP = """# Acme

> A shop for teams.

Each section lists what one audience is served.

## Platform developers

- [README](README.md): what Acme is
- [Guide](https://example.com/guide): the public guide

## Platform operators

- [Ops](ops/README.md): roles and signals

## Tenant users and admins

* [Nouns](om/README.md): the nouns
"""


def test_ops_26_a_well_formed_map_passes(tmp_path):
    files = {"llms.txt": MAP, "README.md": "", "ops/README.md": "", "om/README.md": ""}
    assert found(tmp_path, "OPS-26", files) == (0, [])


def test_ops_26_a_missing_map_fails(tmp_path):
    assert found(tmp_path, "OPS-26", {"llms.txt": None}) == (1, [("OPS-26", "llms.txt", 1)])


def test_ops_26_a_fourth_section_a_bare_link_and_a_dangling_target_fail(tmp_path):
    text = MAP + "\n## Partners\n\n- [Deals](docs/deals.md)\n"
    files = {"llms.txt": text, "README.md": "", "ops/README.md": "", "om/README.md": None}
    code, where = found(tmp_path, "OPS-26", files)
    assert code == 1
    assert sorted(line for _, _, line in where) == [18, 20, 22]


def test_ops_26_a_map_saved_with_a_bom_is_read(tmp_path):
    files = {"llms.txt": "﻿" + MAP, "README.md": "", "ops/README.md": "", "om/README.md": ""}
    assert found(tmp_path, "OPS-26", files) == (0, [])
