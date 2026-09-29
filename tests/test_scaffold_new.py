"""scaffold/new.py: the name it takes, the forms it writes, and the copy it makes."""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCAFFOLD = ROOT / "scaffold"


def load():
    spec = importlib.util.spec_from_file_location("scaffold_new", SCAFFOLD / "new.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["scaffold_new"] = module
    spec.loader.exec_module(module)
    return module


new = load()


@pytest.mark.parametrize("name", ["pressroom", "free_press", "x1_y2"])
def test_one_or_two_snake_case_words_are_a_name(name):
    assert new.refusal(name) is None


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        ("Pressroom", "snake_case"),
        ("free-press", "snake_case"),
        ("free_press_now", "snake_case"),
        ("2press", "snake_case"),
        ("_press", "snake_case"),
        ("json", "shadow"),
        ("email", "shadow"),
        ("class", "shadow"),
        ("acme", "scaffold's own name"),
    ],
)
def test_a_name_that_is_not_one_is_refused(name, reason):
    refused = new.refusal(name)
    assert refused is not None and reason in refused


@pytest.mark.parametrize(
    ("text", "renamed"),
    [
        ("ACME_DATABASE_URL", "FREE_PRESS_DATABASE_URL"),
        ("from acme.om.base import new_id", "from free_press.om.base import new_id"),
        ("acme-om", "free-press-om"),
        ("uv run acme-api serve", "uv run free-press-api serve"),
        ("https://api.staging.acme.example", "https://api.staging.free-press.example"),
        ("bob@ajax.acme.test", "bob@ajax.free-press.test"),
        ("acme_runtime", "free_press_runtime"),
        ("om/src/acme/om", "om/src/free_press/om"),
        ('package = "acme"', 'package = "free_press"'),
        ("AcmeReaders", "FreePressReaders"),
        ("Welcome to Acme.", "Welcome to Free Press."),
        ("x-acme", "x-free-press"),
    ],
)
def test_each_form_of_the_placeholder_takes_its_form_of_the_name(text, renamed):
    assert new.rename(text, new.Names("free_press")) == renamed


def test_a_name_that_holds_the_placeholder_is_written_once():
    assert new.rename("acme ACME Acme acme-x", new.Names("acmeco")) == "acmeco ACMECO Acmeco acmeco-x"


def fixture(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "source"
    for rel, content in {
        "om/src/acme/om/__init__.py": "from acme.om import base\n",
        "deployment/local/grafana/acme-overview.json": '{"title": "Acme overview"}\n',
        "Makefile": "ARCH_CHECK ?= uvx --from git+https://github.com/o/swe_guidelines@v0.1.0 arch-check\n",
        ".env.example": "ACME_ENVIRONMENT=local\n",
        ".env": "ACME_SECRET=mine\n",
        ".venv/lib/acme.py": "x\n",
        "apps/portal/node_modules/acme/index.js": "x\n",
        "om/src/acme/__pycache__/base.cpython-314.pyc": "x\n",
    }.items():
        path = source / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    (source / "docs").mkdir()
    (source / "docs" / "logo.png").write_bytes(b"\x89PNG\0acme\0")
    (source / "scripts").mkdir()
    (source / "scripts" / "dev.sh").write_text("#!/bin/sh\necho acme\n", encoding="utf-8")
    (source / "scripts" / "dev.sh").chmod(0o755)
    plugin = tmp_path / "plugin.json"
    plugin.write_text(json.dumps({"version": "9.8.7"}), encoding="utf-8")
    return source, plugin


def test_the_copy_renames_paths_and_text_and_keeps_binary_files(tmp_path, capsys):
    source, plugin = fixture(tmp_path)
    dest = tmp_path / "out" / "pressroom"
    assert new.main([str(dest)], source=source, plugin=plugin) == 0
    assert (dest / "om/src/pressroom/om/__init__.py").read_text() == "from pressroom.om import base\n"
    assert (dest / "deployment/local/grafana/pressroom-overview.json").read_text() == '{"title": "Pressroom overview"}\n'
    assert (dest / ".env.example").read_text() == "PRESSROOM_ENVIRONMENT=local\n"
    assert (dest / "docs/logo.png").read_bytes() == b"\x89PNG\0acme\0"
    assert (dest / "scripts/dev.sh").stat().st_mode & 0o111
    out = capsys.readouterr().out
    assert "pinned at guideline v9.8.7" in out and "make setup && make check" in out


def test_the_copy_pins_the_release_this_checkout_carries(tmp_path):
    source, plugin = fixture(tmp_path)
    dest = tmp_path / "pressroom"
    assert new.main([str(dest)], source=source, plugin=plugin) == 0
    assert "swe_guidelines@v9.8.7 arch-check" in (dest / "Makefile").read_text()


def test_the_copy_leaves_out_what_a_tool_left_and_local_settings(tmp_path):
    source, plugin = fixture(tmp_path)
    dest = tmp_path / "pressroom"
    assert new.main([str(dest)], source=source, plugin=plugin) == 0
    found = {p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file() and ".git/" not in p.as_posix()}
    assert not any(part in found for part in (".env", ".venv", "node_modules"))
    assert not any(".venv/" in p or "node_modules/" in p or "__pycache__" in p for p in found)


def test_the_copy_is_a_repository_with_nothing_staged(tmp_path):
    source, plugin = fixture(tmp_path)
    dest = tmp_path / "pressroom"
    assert new.main([str(dest)], source=source, plugin=plugin) == 0
    staged = subprocess.run(
        ["git", "-C", str(dest), "diff", "--cached", "--name-only"], capture_output=True, text=True, check=True
    ).stdout
    untracked = subprocess.run(
        ["git", "-C", str(dest), "status", "--porcelain"], capture_output=True, text=True, check=True
    ).stdout
    assert (dest / ".git").is_dir() and staged == "" and "?? " in untracked


def test_a_destination_that_holds_anything_is_refused(tmp_path, capsys):
    source, plugin = fixture(tmp_path)
    dest = tmp_path / "pressroom"
    dest.mkdir()
    (dest / "notes.txt").write_text("mine", encoding="utf-8")
    assert new.main([str(dest)], source=source, plugin=plugin) == 2
    assert "is not an empty folder" in capsys.readouterr().err
    assert [p.name for p in dest.iterdir()] == ["notes.txt"]


def test_an_empty_destination_is_taken(tmp_path):
    source, plugin = fixture(tmp_path)
    dest = tmp_path / "pressroom"
    dest.mkdir()
    assert new.main([str(dest)], source=source, plugin=plugin) == 0


def test_a_refused_name_writes_nothing(tmp_path, capsys):
    source, plugin = fixture(tmp_path)
    assert new.main([str(tmp_path / "Pressroom")], source=source, plugin=plugin) == 2
    assert not (tmp_path / "Pressroom").exists()
    assert "refused" in capsys.readouterr().err


@pytest.mark.parametrize("name", ["pressroom", "free_press"])
def test_a_copy_of_the_scaffold_names_the_placeholder_nowhere(tmp_path, name):
    dest = tmp_path / name
    assert new.main([str(dest)]) == 0
    left = []
    for path in dest.rglob("*"):
        rel = path.relative_to(dest).as_posix()
        if rel.startswith(".git/") or rel == ".git":
            continue
        if re.search("acme", rel, re.IGNORECASE) or (
            path.is_file()
            and new.is_text(path.read_bytes())
            and re.search("acme", path.read_text(encoding="utf-8"), re.IGNORECASE)
        ):
            left.append(rel)
    assert left == []
