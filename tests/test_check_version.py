"""scripts/check_version.py: every copy of the version agrees with plugin.json."""

import pytest


@pytest.fixture
def version(repo):
    return repo.script("check_version")


def test_agreeing_copies_pass(repo, version, capsys):
    assert version.main() == 0
    assert "version ok: 1.2.3" in capsys.readouterr().out


def test_marketplace_mismatch_fails(repo, version, capsys):
    repo.edit(".claude-plugin/marketplace.json", '"version": "1.2.3"', '"version": "1.2.4"')
    assert version.main() == 1
    assert "plugins[0].version is '1.2.4', plugin.json says '1.2.3'" in capsys.readouterr().out


def test_changelog_latest_release_must_match(repo, version, capsys):
    repo.edit("CHANGELOG.md", "## 1.2.3 (2026-01-01)", "## 1.3.0 (2026-02-01)")
    assert version.main() == 1
    assert "CHANGELOG.md:5: latest release is 1.3.0, plugin.json says 1.2.3" in capsys.readouterr().out


def test_changelog_without_a_release_fails(repo, version, capsys):
    repo.write("CHANGELOG.md", "# Changelog\n\n## Unreleased\n")
    assert version.main() == 1
    assert "no release heading" in capsys.readouterr().out


def test_every_pinned_tag_in_adopting_must_match(repo, version, capsys):
    repo.edit("docs/adopting.md", "pinned at `v1.2.3`", "pinned at `v1.2.2`")
    assert version.main() == 1
    assert "docs/adopting.md:3: pins v1.2.2, plugin.json says 1.2.3" in capsys.readouterr().out


def test_plugin_version_must_be_semver(repo, version, capsys):
    repo.write(".claude-plugin/plugin.json", '{"version": "v1.2"}\n')
    assert version.main() == 1
    assert "is not MAJOR.MINOR.PATCH" in capsys.readouterr().out
