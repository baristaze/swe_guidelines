"""scaffold/new.py: the name it takes, the forms it writes, and the copy it makes."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
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
        ("X-Acme-Edge", "X-Free-Press-Edge"),
        ("X-ACME-TOKEN", "X-FREE-PRESS-TOKEN"),
        ("Smoke@Platform.Acme.Invalid", "Smoke@Platform.Free-Press.Invalid"),
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


def test_a_link_stays_a_link_with_its_target_renamed(tmp_path):
    source, plugin = fixture(tmp_path)
    (source / ".agents/skills/acme-watch").mkdir(parents=True)
    (source / ".agents/skills/acme-watch/SKILL.md").write_text("# acme-watch\n", encoding="utf-8")
    (source / ".claude").mkdir()
    (source / ".claude/skills").symlink_to("../.agents/skills")
    (source / "docs/acme.md").symlink_to("../.agents/skills/acme-watch/SKILL.md")
    dest = tmp_path / "pressroom"
    assert new.main([str(dest)], source=source, plugin=plugin) == 0
    link = dest / ".claude/skills"
    assert link.is_symlink() and os.readlink(link) == "../.agents/skills"
    assert (link / "pressroom-watch" / "SKILL.md").read_text() == "# pressroom-watch\n"
    assert os.readlink(dest / "docs/pressroom.md") == "../.agents/skills/pressroom-watch/SKILL.md"


def test_a_scaffold_whose_claude_skills_is_a_folder_still_gives_a_copy_the_link(tmp_path):
    """A plugin staged without its links, as the benchmark stages one, holds
    `.claude/skills` as a folder of copies; the copy still gets the link."""
    source, plugin = fixture(tmp_path)
    for folder in (".agents/skills/acme-watch", ".claude/skills/acme-watch"):
        (source / folder).mkdir(parents=True)
        (source / folder / "SKILL.md").write_text("# acme-watch\n", encoding="utf-8")
    dest = tmp_path / "pressroom"
    assert new.main([str(dest)], source=source, plugin=plugin) == 0
    link = dest / ".claude/skills"
    assert link.is_symlink() and os.readlink(link) == "../.agents/skills"
    assert (link / "pressroom-watch" / "SKILL.md").read_text() == "# pressroom-watch\n"


def test_a_copy_of_the_scaffold_reads_its_skills_from_agents_skills_and_links_claude_skills_to_it(tmp_path):
    dest = tmp_path / "pressroom"
    assert new.main([str(dest)]) == 0
    link = dest / ".claude" / "skills"
    assert link.is_symlink() and os.readlink(link) == "../.agents/skills"
    skills = sorted(p.parent.name for p in (dest / ".agents" / "skills").glob("*/SKILL.md"))
    assert "ops-investigate" in skills and sorted(p.parent.name for p in link.glob("*/SKILL.md")) == skills


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


def test_a_two_word_copy_quotes_its_namespace_in_every_dashboard_search(tmp_path):
    """CloudWatch's SEARCH needs a namespace with a space in double quotes, so
    a two-word product's request and outcome widgets draw on its first deploy."""
    dest = tmp_path / "free_press"
    assert new.main([str(dest)]) == 0
    dashboard = dest / "deployment" / "terraform" / "modules" / "dashboard"
    template = (dashboard / "dashboard.json.tftpl").read_text(encoding="utf-8")
    module = (dashboard / "main.tf").read_text(encoding="utf-8")
    quoted = "SEARCH('{" + '\\"Free Press\\"' + ",OTelLib,"
    assert template.count(quoted) == 2
    assert module.count(quoted) == 1
    assert "SEARCH('{Free" not in template + module


LOCK = """\
---
lockfileVersion: '9.0'

importers:

  .:
    configDependencies: {}
    packageManagerDependencies:
      pnpm:
        specifier: 12.4.2
        version: 12.4.2

---
lockfileVersion: '9.0'

importers:

  apps/portal:
    dependencies:
      '@acme/client':
        specifier: workspace:*
        version: link:../../clients/typescript
      '@fontsource-variable/inter':
        specifier: ^5.3.0
        version: 5.3.0
      '@sentry/react':
        specifier: ^10.74.0
        version: 10.74.0(react@19.3.0)
      react:
        specifier: ^19.3.0
        version: 19.3.0
    devDependencies:
      vite:
        specifier: ^8.3.0
        version: 8.3.0

packages:

  '@sentry/react@10.74.0':
    resolution: {integrity: sha512-0}
    peerDependencies:
      react: ^19.0.0
      '@acme/client': '*'
"""
"""A lockfile as pnpm writes it for the scaffold: two documents, and the
workspace package first among the portal's dependencies, where `@acme` sorts."""


def lock_groups(lock: str) -> list[list[str]]:
    """The names of each group of dependencies under `importers:`, in the
    order the lockfile lists them."""
    groups = []
    for importers in re.findall(r"^importers:\n(.*?)(?=^\S|\Z)", lock, re.MULTILINE | re.DOTALL):
        for rows in re.findall(r"^    \w+:\n((?:      .*\n)+)", importers, re.MULTILINE):
            groups.append(re.findall(r"^      '?([^' ][^':]*)'?:$", rows, re.MULTILINE))
    return groups


@pytest.mark.parametrize(
    ("name", "portal"),
    [
        ("abacus", ["@abacus/client", "@fontsource-variable/inter", "@sentry/react", "react"]),
        ("pressroom", ["@fontsource-variable/inter", "@pressroom/client", "@sentry/react", "react"]),
        ("tidewater", ["@fontsource-variable/inter", "@sentry/react", "@tidewater/client", "react"]),
    ],
)
def test_the_copys_lockfile_lists_the_workspace_package_where_the_copys_name_sorts(tmp_path, name, portal):
    """pnpm writes each importer's dependencies by name, so a package left
    where the placeholder sorted is moved by the first install that writes
    the lockfile. The rows under a name move with it, and nothing outside
    `importers:` moves."""
    source, plugin = fixture(tmp_path)
    (source / "pnpm-lock.yaml").write_text(LOCK, encoding="utf-8")
    dest = tmp_path / name
    assert new.main([str(dest)], source=source, plugin=plugin) == 0
    lock = (dest / "pnpm-lock.yaml").read_text(encoding="utf-8")
    assert lock_groups(lock) == [["pnpm"], portal, ["vite"]]
    assert f"      '@{name}/client':\n        specifier: workspace:*\n        version: link:../../clients/typescript\n" in lock
    assert lock.endswith(f"    peerDependencies:\n      react: ^19.0.0\n      '@{name}/client': '*'\n")
    assert sorted(lock.split("\n")) == sorted(new.rename(LOCK, new.Names(name)).split("\n"))


@pytest.mark.parametrize("name", ["pressroom", "free_press"])
def test_a_copy_of_the_scaffold_lists_each_importers_dependencies_by_name(tmp_path, name):
    dest = tmp_path / name
    assert new.main([str(dest)]) == 0
    lock = (dest / "pnpm-lock.yaml").read_text(encoding="utf-8")
    groups = lock_groups(lock)
    assert any(f"@{name}/client" in group for group in groups)
    assert [group for group in groups if group != sorted(group)] == []
    source = (SCAFFOLD / "acme_root" / "pnpm-lock.yaml").read_text(encoding="utf-8")
    assert sorted(lock.split("\n")) == sorted(new.rename(source, new.Names(name)).split("\n"))


def test_a_lockfile_pnpm_wrote_is_left_as_it_is():
    """The scaffold's own lockfile is pnpm's, so its order is pnpm's: the
    copy's order and pnpm's are one rule."""
    source = (SCAFFOLD / "acme_root" / "pnpm-lock.yaml").read_text(encoding="utf-8")
    assert new.in_pnpm_order(source) == source


def test_a_name_past_the_bound_is_refused_and_the_refusal_says_the_bound():
    assert new.MAX_NAME_LENGTH == 17
    assert new.refusal("independent_press") is None  # exactly at the bound
    refused = new.refusal("independent_presss")
    assert refused is not None and "17 characters" in refused
    assert "independent-presss-production-api" in refused and "32" in refused


def test_the_bound_is_the_tightest_name_the_scaffold_builds():
    """The target group is `<name>-<environment>-api`, the environments are
    staging and production, and AWS holds a target group to 32 characters."""
    terraform = SCAFFOLD / "acme_root" / "deployment" / "terraform"
    balancer = (terraform / "modules" / "load_balancer" / "main.tf").read_text(encoding="utf-8")
    assert 'name        = "acme-${var.environment}-api"' in balancer
    environments = sorted(p.name for p in (terraform / "environments").iterdir() if p.is_dir())
    assert environments == ["prod", "staging"]
    assert new.TIGHTEST_NAME.format(name="acme") == "acme-production-api"


def copy_lint(dest: Path) -> subprocess.CompletedProcess[str]:
    """The copy's own line rule, at the ruff its lock names, over the Python
    `make setup` formats first."""
    lock = (dest / "uv.lock").read_text(encoding="utf-8")
    found = re.search(r'name = "ruff"\nversion = "([^"]+)"', lock)
    assert found, "the copy's lock names no ruff"
    ruff = ["uvx", f"ruff@{found.group(1)}"]
    subprocess.run([*ruff, "format", "--quiet", "."], cwd=dest, check=True)
    return subprocess.run(
        [*ruff, "check", "--select", "E501", "--output-format", "concise", "."],
        cwd=dest,
        capture_output=True,
        text=True,
    )


@pytest.mark.skipif(shutil.which("uvx") is None, reason="needs uvx, which CI has")
@pytest.mark.parametrize("name", ["independent_press", "free_journalism"])
def test_a_copy_under_a_long_name_keeps_every_line_within_its_lint(tmp_path, name):
    dest = tmp_path / name
    assert new.main([str(dest)]) == 0
    linted = copy_lint(dest)
    assert linted.returncode == 0, linted.stdout


def checkout(tmp_path: Path) -> tuple[Path, Path, str]:
    """A clean git checkout of a guideline: its scaffold, its plugin manifest, and its head."""
    repo = tmp_path / "swe_guidelines"
    source = repo / "scaffold" / "acme_root"
    (source / "om/src/acme/om").mkdir(parents=True)
    (source / "om/src/acme/om/__init__.py").write_text("from acme.om import base\n", encoding="utf-8")
    (source / "README.md").write_text("# Acme\n", encoding="utf-8")
    plugin = repo / ".claude-plugin" / "plugin.json"
    plugin.parent.mkdir()
    plugin.write_text(json.dumps({"version": "9.8.7", "repository": "https://github.com/o/guide"}), encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "one"], check=True)
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True)
    return source, plugin, head.stdout.strip()


@pytest.fixture
def identity(monkeypatch):
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "test@example.invalid")


def git_out(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def test_a_copy_from_a_clean_checkout_starts_at_its_base(tmp_path, capsys, identity):
    source, plugin, head = checkout(tmp_path)
    dest = tmp_path / "out" / "pressroom"
    assert new.main([str(dest)], source=source, plugin=plugin) == 0
    assert git_out(dest, "rev-list", "--count", "HEAD") == "1"
    assert git_out(dest, "rev-parse", "scaffold") == git_out(dest, "rev-parse", "HEAD")
    assert git_out(dest, "status", "--porcelain") == ""
    message = git_out(dest, "log", "-1", "--format=%B")
    assert f"Scaffold-Commit: {head}" in message
    assert "Scaffold-Source: https://github.com/o/guide" in message and "Scaffold-Name: pressroom" in message
    assert f"base: the first commit, on scaffold and the main branch, is the scaffold at {head[:9]}" in capsys.readouterr().out


def test_the_base_a_copy_starts_at_names_what_base_py_reads(tmp_path, identity):
    spec = importlib.util.spec_from_file_location("scaffold_base_for_new", SCAFFOLD / "base.py")
    assert spec is not None and spec.loader is not None
    base = importlib.util.module_from_spec(spec)
    sys.modules["scaffold_base_for_new"] = base
    spec.loader.exec_module(base)
    source, plugin, head = checkout(tmp_path)
    dest = tmp_path / "pressroom"
    assert new.main([str(dest)], source=source, plugin=plugin) == 0
    found = base.trailers(git_out(dest, "log", "-1", "--format=%B"))
    assert found == {base.TRAILER_SOURCE: "https://github.com/o/guide", base.TRAILER_COMMIT: head, base.TRAILER_NAME: "pressroom"}
    assert base.base_of(dest) == git_out(dest, "rev-parse", "HEAD") and new.BRANCH == base.BRANCH


def test_a_copy_from_a_checkout_with_changes_of_its_own_records_no_base(tmp_path, capsys, identity):
    source, plugin, _ = checkout(tmp_path)
    (source / "README.md").write_text("# Acme, changed\n", encoding="utf-8")
    dest = tmp_path / "pressroom"
    assert new.main([str(dest)], source=source, plugin=plugin) == 0
    assert subprocess.run(["git", "-C", str(dest), "rev-parse", "--verify", "--quiet", "HEAD"], check=False).returncode != 0
    assert "base: not recorded, since this is not a clean checkout" in capsys.readouterr().out


def test_a_copy_where_git_has_no_identity_records_no_base_and_stages_nothing(tmp_path, capsys, monkeypatch):
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "test@example.invalid")
    source, plugin, _ = checkout(tmp_path)
    for key in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL", "EMAIL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-global"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "user.useConfigOnly")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "true")
    dest = tmp_path / "pressroom"
    assert new.main([str(dest)], source=source, plugin=plugin) == 0
    assert git_out(dest, "diff", "--cached", "--name-only") == ""
    assert "base: not recorded, since git has no identity here" in capsys.readouterr().out
