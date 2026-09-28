"""The subject's output, published whole: the zip of its last commit, and a manifest beside it.

The harness makes the zip with `git archive --format=zip` where the
subject ran, and brings it back into the repeat's artifacts as
`output.zip`. Beside it, `MANIFEST.txt` names every file of the zip, one
line each: its SHA-256, its size in bytes, and its path, in path order.
So the outputs of two runs diff as text. `results.json` records the
zip's own SHA-256, its size, and how many files it holds.

A zip is redacted member by member (`redact.redact_blob`), because a key
inside a compressed member is not in the zip's bytes as it was written.
The harness redacts the zip before it takes the hash and the manifest.
When `run.py redact` rewrites a zip later, `refresh` writes its manifest
and its record in `results.json` again, so both describe the zip that is
published.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any

ZIP = "output.zip"
MANIFEST = "MANIFEST.txt"


def digest(path: Path) -> str:
    """The SHA-256 of a file, read in chunks."""
    sha = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            sha.update(chunk)
    return sha.hexdigest()


def readable(path: Path) -> bool:
    """Whether a file opens as a zip; one redaction replaced does not."""
    try:
        with zipfile.ZipFile(path):
            return True
    except (OSError, zipfile.BadZipFile):
        return False


def manifest_text(path: Path) -> str:
    """One line per file of the zip, in path order: its SHA-256, its size in bytes, and its path.

    A zip that does not open has no line.
    """
    if not readable(path):
        return ""
    lines = []
    with zipfile.ZipFile(path) as zf:
        for info in sorted(zf.infolist(), key=lambda i: i.filename):
            if info.is_dir():
                continue
            lines.append(f"{hashlib.sha256(zf.read(info)).hexdigest()}  {info.file_size}  {info.filename}")
    return "".join(f"{line}\n" for line in lines)


def write_manifest(path: Path) -> Path:
    """Write `MANIFEST.txt` beside the zip and return its path."""
    manifest = Path(path).with_name(MANIFEST)
    manifest.write_text(manifest_text(path), encoding="utf-8")
    return manifest


def record(path: Path, run_dir: Path) -> dict[str, Any]:
    """What `results.json` records of a zip: its path and its manifest's in the run folder, its hash, its size, its files.

    A zip that does not open holds no file the record can name.
    """
    path = Path(path)
    files = 0
    if readable(path):
        with zipfile.ZipFile(path) as zf:
            files = sum(1 for info in zf.infolist() if not info.is_dir())
    return {
        "path": path.relative_to(run_dir).as_posix(),
        "manifest": path.with_name(MANIFEST).relative_to(run_dir).as_posix(),
        "sha256": digest(path),
        "bytes": path.stat().st_size,
        "files": files,
    }


def refresh(path: Path) -> None:
    """After a zip was written again: its manifest, when it has one, and its record in the run's `results.json`.

    A zip redaction replaced, because it could not be scanned, is no zip
    any more: its manifest is emptied, and its record says it holds no file.
    """
    path = Path(path)
    manifest = path.with_name(MANIFEST)
    if manifest.is_file():
        write_manifest(path)
    for folder in path.parents:
        results = folder / "results.json"
        if not results.is_file():
            continue
        data = json.loads(results.read_text(encoding="utf-8"))
        rel = path.relative_to(folder).as_posix()
        changed = False
        for repeat in data.get("repeats", []) if isinstance(data, dict) else []:
            archive = repeat.get("archive") if isinstance(repeat, dict) else None
            if isinstance(archive, dict) and archive.get("path") == rel:
                archive.update(sha256=digest(path), bytes=path.stat().st_size)
                if not readable(path):
                    archive["files"] = 0
                changed = True
        if changed:
            results.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return
