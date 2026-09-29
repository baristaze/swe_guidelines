"""The session file holds a token, so it is owner-only from the moment it
exists: created with mode 0600, in a directory created with mode 0700, not
made readable first and locked down after."""

import os
from pathlib import Path

import pytest

from acme.apps.cli import config


def test_the_session_file_is_owner_only_from_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ACME_HOME", str(tmp_path / "home" / "acme"))
    # A chmod after the write is too late to count; the mode must be right at creation.
    monkeypatch.setattr(Path, "chmod", lambda self, mode, **kwargs: None)
    session = config.Session(
        api_url="http://test",
        token="sess_secret",
        email="ann@example.test",
        display_name="Ann",
        org_slug="ajax",
        org_name="Ajax",
    )
    previous = os.umask(0o022)
    try:
        path = config.save_session(session)
        assert path.stat().st_mode & 0o777 == 0o600
        assert path.parent.stat().st_mode & 0o777 == 0o700
        # Saving again truncates and rewrites the same owner-only file.
        path = config.save_session(session)
        assert path.stat().st_mode & 0o777 == 0o600
    finally:
        os.umask(previous)
    assert config.load_session() == session


def kept_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACME_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ACME_TOKEN", raising=False)
    monkeypatch.delenv("ACME_API_URL", raising=False)
    config.save_session(
        config.Session(
            api_url="https://api.example.test",
            token="ses_kept",
            email="ann@example.test",
            display_name="Ann",
            org_slug="ajax",
            org_name="Ajax",
        )
    )


def test_the_session_token_goes_to_the_api_that_issued_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kept_session(tmp_path, monkeypatch)
    assert config.credentials() == ("https://api.example.test", "ses_kept")
    assert config.credentials("https://api.example.test/") == (
        "https://api.example.test",
        "ses_kept",
    )


@pytest.mark.parametrize("from_env", [False, True])
def test_another_api_is_refused_the_session_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, from_env: bool
) -> None:
    kept_session(tmp_path, monkeypatch)
    if from_env:
        monkeypatch.setenv("ACME_API_URL", "http://localhost:8000")
    with pytest.raises(config.BadSetting, match=r"issued by https://api\.example\.test"):
        config.credentials(None if from_env else "http://localhost:8000")


def test_the_environments_token_never_goes_to_the_files_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kept_session(tmp_path, monkeypatch)
    monkeypatch.setenv("ACME_TOKEN", "key_ci")
    assert config.credentials() == (config.DEFAULT_API_URL, "key_ci")
    assert config.credentials("https://other.example.test") == (
        "https://other.example.test",
        "key_ci",
    )
