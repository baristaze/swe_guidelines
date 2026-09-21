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


def test_tag_that_matches_the_manifest_passes(repo, version, capsys):
    assert version.main(["--tag", "v1.2.3"]) == 0
    assert "version ok: 1.2.3, tag v1.2.3" in capsys.readouterr().out


@pytest.mark.parametrize("tag", ["v1.2.4", "1.2.3", "v1.2.3-rc1", "refs/tags/v1.2.3"])
def test_tag_that_differs_from_the_manifest_fails(repo, version, capsys, tag):
    assert version.main(["--tag", tag]) == 1
    assert f"tag {tag!r} does not match plugin.json, which says v1.2.3" in capsys.readouterr().out


def test_tag_without_a_value_is_refused(repo, version):
    with pytest.raises(SystemExit) as exit_:
        version.main(["--tag"])
    assert exit_.value.code == 2


def test_checker_distribution_version_must_match(repo, version, capsys):
    repo.edit("checkers/pyproject.toml", 'version = "1.2.3"', 'version = "1.2.4"')
    assert version.main() == 1
    assert "checkers/pyproject.toml: version is 1.2.4, plugin.json says 1.2.3" in capsys.readouterr().out


def test_checker_package_version_must_match(repo, version, capsys):
    repo.edit("checkers/src/arch_check/__init__.py", '__version__ = "1.2.3"', '__version__ = "1.2.2"')
    assert version.main() == 1
    assert "checkers/src/arch_check/__init__.py: __version__ is 1.2.2, plugin.json says 1.2.3" in capsys.readouterr().out


def test_checker_package_without_a_version_fails(repo, version, capsys):
    repo.write("checkers/src/arch_check/__init__.py", '"""arch-check."""\n')
    assert version.main() == 1
    assert "checkers/src/arch_check/__init__.py: no __version__" in capsys.readouterr().out


def test_every_pinned_tag_in_the_checker_readme_must_match(repo, version, capsys):
    repo.edit("checkers/README.md", "@v1.2.3#", "@v1.2.0#")
    assert version.main() == 1
    assert "checkers/README.md:3: pins v1.2.0, plugin.json says 1.2.3" in capsys.readouterr().out


def test_every_pinned_tag_in_the_readme_must_match(repo, version, capsys):
    repo.edit("README.md", "`v1.2.3`", "`v1.2.1`")
    assert version.main() == 1
    assert "README.md:3: pins v1.2.1, plugin.json says 1.2.3" in capsys.readouterr().out
