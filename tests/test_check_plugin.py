"""scripts/check_plugin.py: plugin.json validated, strictly but for the CLAUDE.md warning."""

import pytest


@pytest.fixture
def plugin(repo):
    return repo.script("check_plugin")


def test_the_expected_warning_alone_passes(plugin):
    output = "⚠ Found 1 warning:\n\n  \u276f root: CLAUDE.md at the plugin root is not loaded as project context. To ship...\n"
    assert plugin.problems(output) == []


def test_any_other_warning_or_error_fails(plugin):
    output = (
        "  \u276f root: CLAUDE.md at the plugin root is not loaded as project context.\n"
        "  \u276f version: not a semantic version\n"
    )
    assert plugin.problems(output) == ["\u276f version: not a semantic version"]
