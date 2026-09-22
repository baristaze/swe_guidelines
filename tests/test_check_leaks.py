"""scripts/check_leaks.py: refused vocabulary per file scope."""

import pytest


@pytest.fixture
def leaks(repo):
    return repo.script("check_leaks")


def test_valid_tree_passes(repo, leaks, capsys):
    assert leaks.main() == 0
    assert "leaks ok" in capsys.readouterr().out


def test_product_term_in_guideline_fails(repo, leaks, capsys):
    repo.edit("architecture.md", "One table per entity.", "One table per robot.")
    assert leaks.main() == 1
    assert "architecture.md:32: product term 'robot'" in capsys.readouterr().out


@pytest.mark.parametrize("name", ["CLAUDE.md", "SECURITY.md"])
def test_the_contributor_files_are_scanned_for_product_terms(repo, leaks, capsys, name):
    repo.write(name, "# Notes\n\nOne table per robot.\n")
    assert leaks.main() == 1
    assert name in capsys.readouterr().out


def test_agent_vocabulary_is_allowed_everywhere(repo, leaks):
    repo.edit("skills/arch-review-om/SKILL.md", "Never edit", "The agent never edits")
    repo.edit("lenses/om.md", "Tables holding two entities.", "Tables an agent holds.")
    repo.edit("architecture.md", "One table per entity.", "One table per entity, for people and agents.")
    assert leaks.main() == 0


def test_history_phrasing_fails_in_the_guideline_only(repo, leaks, capsys):
    repo.edit("lenses/om.md", "One table per entity.", "One table per entity, as previously.")
    assert leaks.main() == 0
    repo.edit("architecture.md", "One table per entity.", "One table per entity, as previously.")
    assert leaks.main() == 1
    assert "history term 'previously'" in capsys.readouterr().out


def test_a_skill_is_scanned_once(repo, leaks, capsys):
    repo.edit("skills/arch-review-om/SKILL.md", "Never edit", "Never edit the firmware;")
    assert leaks.main() == 1
    out = capsys.readouterr().out
    assert out.count("product term 'firmware'") == 1
    assert "1 leak(s)" in out


def test_terms_are_matched_case_insensitively_on_word_boundaries(repo, leaks):
    repo.edit("architecture.md", "One table per entity.", "One table per Labs entry; syllabus is fine.")
    assert leaks.main() == 1
    repo.edit("architecture.md", "One table per Labs entry; syllabus is fine.", "The syllabus is fine.")
    assert leaks.main() == 0


def test_product_term_fails_under_agents(repo, leaks, capsys):
    repo.write("agents/arch-reviewer.md", "---\nname: arch-reviewer\n---\n\nJudge the firmware.\n")
    assert leaks.main() == 1
    assert "agents/arch-reviewer.md:5: product term 'firmware'" in capsys.readouterr().out
    repo.write("agents/arch-reviewer.md", "---\nname: arch-reviewer\n---\n\nJudge one lens group.\n")
    assert leaks.main() == 0


@pytest.mark.parametrize("rel", ["docs/sub/x.md", "skills/a/b/c/d.md", "benchmark/a/b/c.md", "lenses/sub/x.md"])
def test_a_file_at_any_depth_of_a_scoped_directory_is_scanned(repo, leaks, capsys, rel):
    repo.write(rel, "# Deep\n\nThe firmware.\n")
    assert leaks.main() == 1
    assert f"{rel}:3: product term 'firmware'" in capsys.readouterr().out


def test_benchmark_runs_and_files_outside_every_scope_are_not_scanned(repo, leaks):
    repo.write("benchmark/runs/one/report.md", "# Run\n\nThe firmware.\n")
    repo.write("CHANGELOG.md", "# Changelog\n\nThe firmware, previously.\n")
    repo.write("docs/node_modules/pkg/README.md", "# Pkg\n\nThe firmware.\n")
    assert leaks.main() == 0


def test_product_term_fails_in_agents_md(repo, leaks, capsys):
    repo.write("AGENTS.md", "# Working here\n\nNo firmware talk.\n")
    assert leaks.main() == 1
    assert "AGENTS.md:3: product term 'firmware'" in capsys.readouterr().out
    repo.write("AGENTS.md", "# Working here\n\nAgents are agents.\n")
    assert leaks.main() == 0


def test_a_copy_built_from_a_dump_with_model_copy_fails_in_every_snippet(repo, leaks, capsys):
    repo.edit("lenses/om.md", "One table per entity.", "`current.model_copy(update={**caller.model_dump()})`")
    assert leaks.main() == 1
    assert "shape term 'model_copy(update={**'" in capsys.readouterr().out
    repo.edit(
        "lenses/om.md", "`current.model_copy(update={**caller.model_dump()})`", '`current.model_copy(update={"status": s})`'
    )
    assert leaks.main() == 0


def test_the_benchmark_folder_is_scanned_for_product_terms(repo, leaks, capsys):
    repo.write("benchmark/README.md", "# Benchmark\n\nRun it on a station.\n")
    assert leaks.main() == 0
    repo.write("benchmark/README.md", "# Benchmark\n\nRun it on a test bench.\n")
    assert leaks.main() == 1
    assert "benchmark/README.md:3: product term 'bench'" in capsys.readouterr().out


def test_the_word_benchmark_itself_is_not_a_leak(repo, leaks):
    repo.write("benchmark/harness/notes.md", "# Notes\n\nA benchmark run writes benchmarks, not benches.\n")
    assert leaks.main() == 1  # "benches" is the refused word, "benchmark" is not
    repo.write("benchmark/harness/notes.md", "# Notes\n\nA benchmark run writes benchmark results.\n")
    assert leaks.main() == 0


def test_reference_name_fails_outside_the_next_section(repo, leaks, capsys):
    repo.write("checkers/src/rule.py", "# modeled on Tadas\n")
    assert leaks.main() == 1
    assert "checkers/src/rule.py:1: reference term 'Tadas'" in capsys.readouterr().out
    repo.write("checkers/src/rule.py", "# modeled on acme\n")
    assert leaks.main() == 0


def test_reference_name_is_allowed_in_the_next_section_and_the_changelog(repo, leaks, capsys):
    text = (repo.root / "architecture.md").read_text(encoding="utf-8")
    repo.write("architecture.md", text + "\n## Next: An End-to-End Reference Implementation\n\nTadas.\n")
    repo.write("CHANGELOG.md", "# Changelog\n\nTadas pinned.\n")
    assert leaks.main() == 0
    repo.write("architecture.md", text + "\n## Next: An End-to-End Reference Implementation\n\nTadas.\n\n## After\n\nTadas.\n")
    assert leaks.main() == 1
    assert "reference term 'Tadas'" in capsys.readouterr().out


def test_the_next_section_exempts_the_reference_in_the_guideline_only(repo, leaks, capsys):
    repo.write("docs/notes.md", "# Notes\n\n## Next: An End-to-End Reference Implementation\n\nTadas.\n")
    assert leaks.main() == 1
    assert "docs/notes.md:5: reference term 'Tadas'" in capsys.readouterr().out
    repo.write("docs/notes.md", "# Notes\n\n## Next: An End-to-End Reference Implementation\n\nThe reference.\n")
    assert leaks.main() == 0


@pytest.mark.parametrize("rel", [".github/PULL_REQUEST_TEMPLATE.md", ".github/ISSUE_TEMPLATE/bug-report.md"])
def test_the_github_templates_are_scanned_for_product_terms(repo, leaks, capsys, rel):
    repo.write(rel, "# Report\n\nWhat did the lens miss?\n")
    assert leaks.main() == 0
    repo.write(rel, "# Report\n\nWhich robot were you on?\n")
    assert leaks.main() == 1
    assert f"{rel}:3: product term 'robot'" in capsys.readouterr().out


@pytest.mark.parametrize("rel", ["Makefile", "benchmark/runtime/Dockerfile", ".markdownlint-cli2.jsonc", "LICENSE"])
def test_reference_name_fails_in_a_text_file_of_any_suffix(repo, leaks, capsys, rel):
    repo.write(rel, "# built like the reference\n")
    assert leaks.main() == 0
    repo.write(rel, "# built like Tadas\n")
    assert leaks.main() == 1
    assert f"{rel}:1: reference term 'Tadas'" in capsys.readouterr().out


def test_a_binary_file_is_not_scanned_for_the_reference_name(repo, leaks):
    (repo.root / "docs").mkdir(exist_ok=True)
    (repo.root / "docs/logo.png").write_bytes(b"\x89PNG\x00Tadas")
    (repo.root / "docs/blob.bin").write_bytes(b"Tadas \xff\xfe")
    assert leaks.main() == 0


def test_claude_code_worktrees_are_not_scanned(repo, leaks):
    repo.write(".claude/worktrees/agent-1/docs/notes.md", "# Notes\n\nThe firmware of Tadas.\n")
    repo.write(".claude/worktrees/agent-1/Makefile", "# Tadas\n")
    assert leaks.main() == 0


@pytest.mark.parametrize(
    "rel",
    [
        ".github/workflows/ci.yml",
        ".github/dependabot.yml",
        ".github/ISSUE_TEMPLATE/config.yaml",
        "benchmark/scenarios/review.yaml",
        "skills/arch-review-om/evals.yaml",
        ".claude-plugin/plugin.json",
    ],
)
def test_a_product_term_in_a_published_yaml_or_json_file_fails(repo, leaks, capsys, rel):
    repo.write(rel, "name: check\n# drives the firmware\n")
    assert leaks.main() == 1
    assert f"{rel}:2: product term 'firmware'" in capsys.readouterr().out


@pytest.mark.parametrize("folder", ["scripts", "checkers/src/arch_check", "benchmark/harness"])
def test_a_product_term_in_a_python_docstring_fails(repo, leaks, capsys, folder):
    rel = f"{folder}/tool.py"
    source = '"""A tool."""\n\nTERMS = ["firmware"]\n\n\ndef run():\n    """Run it.\n\n    Drives the firmware.\n    """\n'
    repo.write(rel, source)
    assert leaks.main() == 1
    out = capsys.readouterr().out
    assert f"{rel}:9: product term 'firmware'" in out
    assert "1 leak(s)" in out


def test_python_code_and_the_tests_are_not_scanned_for_product_terms(repo, leaks):
    repo.write("scripts/tool.py", 'TERMS = [r"\\bfirmware\\b"]  # the firmware\n')
    repo.write("tests/test_tool.py", '"""The firmware case."""\n')
    repo.write("scripts/broken.py", "def (:\n")
    assert leaks.main() == 0
