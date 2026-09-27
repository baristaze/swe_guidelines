"""No key leaves a run folder: every file is scanned and redacted in place.

A run folder holds what a subject printed and wrote, and a subject can
print anything it can read. So before a run folder is shown or uploaded,
every file in it is scanned as bytes, frames included, and two things
are replaced with `[redacted]`: the value of every provider key the
harness knows by name, and anything shaped like a provider key, whether
the harness holds that key or not.

A zip is scanned member by member, names and comment included, because
a compressed member does not hold its text as bytes a scan could see; a
zip inside it is scanned the same way, down to four levels. A zip that
held a key is written again, with each member's time, mode, and
compression as they were, and its manifest and its record in
`results.json` are written again to match (`archive.refresh`). A zip
too deep, or one that would unpack to more than 1 GiB, is not unpacked:
it is replaced by a line that says so, because nothing could say it
holds no key.
"""

from __future__ import annotations

import io
import os
import re
import zipfile
from pathlib import Path

from . import archive as A
from . import providers as P
from .runtime import SUBJECT_KEYS

REDACTED = b"[redacted]"
# A zip inside a zip is read too, down to this many levels.
ZIP_DEPTH = 4
# A zip whose members would unpack to more than this is not unpacked.
ZIP_BYTES = 1 << 30
# What a zip the scan cannot unpack becomes.
UNREADABLE = b"[redacted: a zip that could not be scanned for keys]\n"
# Shorter than this is not a key, and replacing it would redact ordinary words.
MIN_KEY_CHARS = 8
# The shapes of the keys a run could meet: the four providers, GitHub, and AWS.
KEY_SHAPES = re.compile(
    rb"sk-ant-[A-Za-z0-9_\-]{16,}"
    rb"|sk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_\-]{20,}"
    rb"|AIza[0-9A-Za-z_\-]{30,}"
    rb"|xai-[A-Za-z0-9_\-]{20,}"
    rb"|gh[pousr]_[A-Za-z0-9]{30,}"
    rb"|github_pat_[A-Za-z0-9_]{20,}"
    rb"|AKIA[0-9A-Z]{16}"
)


def key_values(env: dict[str, str] | None = None) -> set[str]:
    """The value of every judge key and subject key in the environment."""
    source = dict(os.environ) if env is None else env
    names = (*P.JUDGE_KEY_NAMES, *SUBJECT_KEYS.values())
    return {source[n] for n in names if len(source.get(n) or "") >= MIN_KEY_CHARS}


def redact_bytes(data: bytes, values: set[str]) -> tuple[bytes, int]:
    """The data with every value and every key shape replaced, and how many were."""
    count = 0
    # The longest value first, so a value inside another never leaves a tail.
    for value in sorted(values, key=len, reverse=True):
        raw = value.encode()
        count += data.count(raw)
        data = data.replace(raw, REDACTED)
    data, shaped = KEY_SHAPES.subn(REDACTED, data)
    return data, count + shaped


def _text(value: str, values: set[str]) -> tuple[str, int]:
    clean, count = redact_bytes(value.encode("utf-8"), values)
    return clean.decode("utf-8", errors="replace"), count


class Unreadable(Exception):
    """A zip the scan does not unpack: too deep inside others, or too large once unpacked."""


def _is_zip(data: bytes) -> bool:
    return zipfile.is_zipfile(io.BytesIO(data))


def _open(data: bytes, depth: int) -> zipfile.ZipFile:
    """A zip the scan may unpack; Unreadable when it is too deep or would unpack too large."""
    if depth >= ZIP_DEPTH:
        raise Unreadable(f"a zip inside {depth} others")
    zf = zipfile.ZipFile(io.BytesIO(data))
    if sum(info.file_size for info in zf.infolist()) > ZIP_BYTES:
        zf.close()
        raise Unreadable(f"a zip that unpacks to more than {ZIP_BYTES} bytes")
    return zf


def redact_blob(data: bytes, values: set[str], depth: int = 0) -> tuple[bytes, int]:
    """Bytes with every key redacted, and how many there were.

    A zip is redacted member by member, a zip inside it too, and each
    member's name and the zip's comment with them. It is written again
    only when it held a key, each member keeping its time, its mode, and
    its compression. Then its bytes are scanned as they stand, so a key
    outside what the zip's reader sees is redacted as well. A zip the
    scan cannot unpack becomes a line that says so, since nothing could
    say it holds no key.
    """
    count = 0
    if _is_zip(data):
        try:
            data, count = _redact_members(data, values, depth)
        except Unreadable:
            return UNREADABLE, 1
        except zipfile.BadZipFile:
            pass  # it only looks like a zip; its bytes are scanned below
    clean, raw = redact_bytes(data, values)
    return clean, count + raw


def _redact_members(data: bytes, values: set[str], depth: int) -> tuple[bytes, int]:
    with _open(data, depth) as zf:
        comment, count = redact_bytes(zf.comment, values)
        members = []
        for info in zf.infolist():
            content, found = redact_blob(zf.read(info), values, depth + 1)
            name, named = _text(info.filename, values)
            count += found + named
            members.append((info, name, content))
    if not count:
        return data, 0
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        zf.comment = comment
        for info, name, content in members:
            copy = zipfile.ZipInfo(name, date_time=info.date_time)
            copy.compress_type = info.compress_type
            copy.external_attr = info.external_attr
            copy.create_system = info.create_system
            zf.writestr(copy, content)
    return out.getvalue(), count


def redact_zip(path: str | Path, values: set[str]) -> int:
    """Redact a zip file in place, as `redact_blob` does; return how many keys it held."""
    path = Path(path)
    clean, count = redact_blob(path.read_bytes(), values)
    if count:
        temporary = path.with_name(f".{path.name}.redacting")
        temporary.write_bytes(clean)
        temporary.replace(path)
    return count


def zip_keys(path: str | Path) -> list[str]:
    """Where a zip holds a string shaped like a key: each member, `!` between a zip and a member inside it.

    A zip the scan cannot unpack is named with the reason, and so are
    the zip's comment and its bytes outside what its reader sees.
    """
    return _scan(Path(path).read_bytes(), "", 0)


def _scan(data: bytes, where: str, depth: int) -> list[str]:
    found: list[str] = []
    try:
        with _open(data, depth) as zf:
            if KEY_SHAPES.search(zf.comment):
                found.append(f"{where}(the zip's comment)")
            for info in zf.infolist():
                member = f"{where}{info.filename}"
                content = zf.read(info)
                if KEY_SHAPES.search(info.filename.encode("utf-8")) or KEY_SHAPES.search(content):
                    found.append(member)
                if _is_zip(content):
                    found += _scan(content, f"{member}!", depth + 1)
    except Unreadable as exc:
        return [f"{where}(not scanned: {exc})"]
    if KEY_SHAPES.search(data) and not found:
        found.append(f"{where}(the zip's bytes outside its members)")
    return found


def redact_folder(folder: str | Path, values: set[str]) -> dict[Path, int]:
    """Redact every file under the folder in place; return the files changed and how many keys each held.

    A symlink is not followed: it could point out of the folder, and the
    upload does not follow it either. A zip is redacted as `redact_blob`
    says, and when it changed, its manifest and its record are written
    again.
    """
    found: dict[Path, int] = {}
    for path in sorted(Path(folder).rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        data = path.read_bytes()
        clean, count = redact_blob(data, values)
        if count:
            path.write_bytes(clean)
            if path.name.endswith(".zip"):
                A.refresh(path)
            found[path] = count
    return found
