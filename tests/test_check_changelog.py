"""scripts/check_changelog.py: only a release pull request edits CHANGELOG.md."""

import pytest


@pytest.fixture
def changelog(repo):
    return repo.script("check_changelog")


@pytest.mark.parametrize(
    ("title", "branch"),
    [
        ("Release 0.31.0: the tooling reads what it missed", "tooling-reads-what-it-missed"),
        ("Tooling reads what it missed", "release-0-31-0"),
        ("Tooling reads what it missed", "release-0.31.0"),
    ],
)
def test_a_release_may_edit_the_changelog(changelog, title, branch):
    assert changelog.refusal(["CHANGELOG.md", "README.md"], title, branch) is None


@pytest.mark.parametrize(
    ("title", "branch"),
    [
        ("The SQL replay follows a rename", "sql-replay-follows-a-rename"),
        ("Prepare the release notes", "release-notes-wording"),
        ("Released 0.31.0 notes", "notes"),
        ("release 0.31.0: lower case", "notes"),
    ],
)
def test_any_other_pull_request_that_edits_the_changelog_is_refused(changelog, title, branch):
    message = changelog.refusal(["scripts/x.py", "CHANGELOG.md"], title, branch)
    assert message is not None
    assert "CHANGELOG.md" in message
    assert "Release X.Y.Z" in message


def test_a_pull_request_that_leaves_the_changelog_alone_passes(changelog):
    assert changelog.refusal(["docs/CHANGELOG.md.bak", "scripts/x.py"], "Fix a typo", "typo") is None


def test_main_reads_the_changed_files_from_git(changelog, monkeypatch, capsys):
    monkeypatch.setattr(changelog, "changed_files", lambda base: ["CHANGELOG.md"] if base == "origin/main" else [])
    argv = ["--base", "origin/main", "--title", "A change", "--branch", "a-change"]
    assert changelog.main(argv) == 1
    assert "check_changelog:" in capsys.readouterr().err
    assert changelog.main([*argv[:2], "--title", "Release 1.0.0: stable", *argv[4:]]) == 0


def test_an_unknown_argument_exits_2(changelog):
    with pytest.raises(SystemExit) as e:
        changelog.main(["--bse", "origin/main"])
    assert e.value.code == 2
