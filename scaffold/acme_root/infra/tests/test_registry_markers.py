"""Every composition root and registry a copy adds to holds the scaffold's
entries, one marker line, then the copy's own. A copy's change and the
scaffold's next release then edit lines apart, and git merges the two.

The marker is the same words in every file, so a skill can say "below the
marker". This holds that each root carries it, spelled exactly, and that it
separates: on two branches of a scratch repository, one edits the entry
above every marker and adds one there, the other adds one below, and
`git merge-tree` merges them with no conflict."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

MARKER = "A copy adds its entries below this line."

ROOTS = (
    "om/src/*/om/root.py",
    "om/src/*/om/storage/root.py",
    "om/src/*/om/storage/impl/memory.py",
    "om/src/*/om/storage/impl/postgres.py",
    "om/src/*/om/storage/roles.py",
    "om/src/*/om/storage/scopes.py",
    "services/api/src/*/services/api/services/__init__.py",
    "services/api/src/*/services/api/services/impl/root.py",
    "services/api/src/*/services/api/gateway/resolve.py",
    "services/api/src/*/services/api/routers/__init__.py",
    "workers/maintenance/src/*/workers/maintenance/main.py",
    "apps/portal/src/queries/keys.ts",
    "apps/portal/src/app/routes.tsx",
    "apps/portal/src/realtime/router.ts",
)
"""The roots and registries of the scaffold's own, by a path that holds in a
copy under any name."""

SOURCES = ("om/src", "services", "workers", "apps", "integrations", "infra/src", "ops/src")
SUFFIXES = {".py": "#", ".ts": "//", ".tsx": "//"}


def roots() -> list[Path]:
    found: list[Path] = []
    for pattern in ROOTS:
        matches = sorted(ROOT.glob(pattern))
        assert len(matches) == 1, f"{pattern} names {len(matches)} files"
        found.extend(matches)
    return found


def marked() -> list[Path]:
    """Every source file that carries a marker line, the roots among them."""
    found: list[Path] = []
    for top in SOURCES:
        for path in sorted((ROOT / top).rglob("*")):
            if path.suffix not in SUFFIXES or "node_modules" in path.parts:
                continue
            if MARKER in path.read_text():
                found.append(path)
    return found


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.com", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )


def marker_lines(lines: list[str]) -> list[int]:
    return [i for i, line in enumerate(lines) if line.strip().endswith(MARKER)]


def test_every_root_carries_the_marker() -> None:
    files = marked()
    for path in roots():
        assert path in files, f"{path.relative_to(ROOT)} carries no marker line"


def test_every_marker_is_the_same_words_on_a_line_of_its_own() -> None:
    for path in marked():
        prefix = SUFFIXES[path.suffix]
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            if MARKER in line:
                assert line.strip() == f"{prefix} {MARKER}", (
                    f"{path.relative_to(ROOT)}:{number} is not the marker alone: {line.strip()}"
                )


def test_an_entry_above_every_marker_and_one_below_it_merge(tmp_path: Path) -> None:
    files = marked()
    repo = tmp_path / "tree"
    repo.mkdir()
    assert git(repo, "init", "-q", "-b", "base").returncode == 0
    for path in files:
        target = repo / path.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(path.read_text())
    git(repo, "add", ".")
    assert git(repo, "commit", "-q", "-m", "base").returncode == 0

    def branch(name: str, edit: str) -> None:
        assert git(repo, "checkout", "-q", "-b", name, "base").returncode == 0
        for path in files:
            target = repo / path.relative_to(ROOT)
            lines = target.read_text().splitlines()
            for i in reversed(marker_lines(lines)):
                indent = lines[i][: len(lines[i]) - len(lines[i].lstrip())]
                prefix = SUFFIXES[path.suffix]
                if edit == "scaffold":
                    # The release edits its last entry and adds one after it.
                    lines[i - 1] += f"  {prefix} edited"
                    lines.insert(i, f"{indent}{prefix} an entry of the scaffold's")
                else:
                    lines.insert(i + 1, f"{indent}{prefix} an entry of the copy's")
            target.write_text("\n".join(lines) + "\n")
        git(repo, "add", ".")
        assert git(repo, "commit", "-q", "-m", name).returncode == 0

    branch("scaffold", "scaffold")
    branch("copy", "copy")
    merged = git(
        repo, "merge-tree", "--write-tree", "--name-only", "--no-messages", "scaffold", "copy"
    )
    conflicted = merged.stdout.splitlines()[1:]
    assert merged.returncode == 0, f"the marker does not separate in: {conflicted}"
    assert len(files) >= len(ROOTS)


def test_a_name_below_the_marker_of_a_re_export_list_keeps_ruff_sorted(tmp_path: Path) -> None:
    """Ruff holds `__all__` sorted (RUF022), so a copy's names never join the
    scaffold's list: they go in an `__all__ +=` list the marker opens, sorted
    on their own. A name that sorts before every one of the scaffold's then
    leaves the lint green."""
    lists = 0
    for path in marked():
        if path.suffix != ".py":
            continue
        lines = path.read_text().splitlines()
        found = [i for i in marker_lines(lines) if lines[i - 1].strip() == "__all__ += ["]
        if not found:
            continue
        for i in reversed(found):
            lines.insert(i + 1, '    "AaaServiceInterface",')
            lists += 1
        copied = tmp_path / path.name
        copied.write_text("\n".join(lines) + "\n")
        linted = subprocess.run(
            [
                sys.executable,
                "-m",
                "ruff",
                "check",
                "--no-cache",
                "--select",
                "RUF022",
                str(copied),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert linted.returncode == 0, f"{path.relative_to(ROOT)}: {linted.stdout}"
    assert lists >= 1, "no re-export list opens with the marker"
