"""No key leaves a run folder: every file is scanned and redacted in place.

A run folder holds what a subject printed and wrote, and a subject can
print anything it can read. So before a run folder is shown or uploaded,
every file in it is scanned as bytes, frames included, and two things
are replaced with `[redacted]`: the value of every provider key the
harness knows by name, and anything shaped like a provider key, whether
the harness holds that key or not.

A compressed file does not hold its text as bytes a scan could see. So
the scan unpacks the forms the standard library reads: a zip, member by
member with its members' names and its comment; a tar, member by member
with their names; and a gzip, bzip2, or xz stream. What it unpacks is
scanned the same way, down to four levels, and then the file's bytes
are scanned as they stand. A file that held a key is packed again in its
own form: a zip keeps each member's time, mode, and compression, and its
manifest and its record in `results.json` are written again to match
(`archive.refresh`). A file the scan cannot read is replaced by a line
that says so, because nothing could say it holds no key: one too deep,
one that would unpack past 1 GiB, a member that does not read (an
encrypted one, one in a compression zipfile does not know), a gzip,
bzip2, xz, or tar stream that breaks, and a form the standard library
cannot read (zstd, 7z, rar, lz4). A file that only looks like a zip, and
does not open as one, is scanned as bytes.

A run folder holds no `.git`: the runtime does not collect one, since a
git object is compressed and the output's zip is the record of the
output.
"""

from __future__ import annotations

import bz2
import gzip
import io
import lzma
import os
import re
import struct
import tarfile
import zipfile
import zlib
from pathlib import Path
from typing import Any

from . import archive as A
from . import providers as P
from .runtime import SUBJECT_KEYS

REDACTED = b"[redacted]"
# What the scan unpacks is unpacked again, down to this many levels.
NESTING = 4
# A file that would unpack to more than this is not unpacked.
UNPACKED = 1 << 30
# What a file the scan cannot read becomes.
UNREADABLE = b"[redacted: a file that could not be scanned for keys]\n"
# The first bytes of the compressed forms the standard library cannot read: zstd, 7z, rar, lz4.
OPAQUE = (b"\x28\xb5\x2f\xfd", b"7z\xbc\xaf\x27\x1c", b"Rar!\x1a\x07", b"\x04\x22\x4d\x18")
# The compressions a zip is written back in; a member in any other is stored.
WRITABLE = (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA)
# What reading a broken, encrypted, or unknown archive can raise.
READ_ERRORS = (
    OSError,
    EOFError,
    ValueError,
    RuntimeError,
    NotImplementedError,
    struct.error,
    zipfile.BadZipFile,
    zlib.error,
    lzma.LZMAError,
    tarfile.TarError,
)
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
    """A file the scan does not read: too deep, too large once unpacked, or broken."""


def form(data: bytes) -> str | None:
    """The form a file's bytes are in, of those the scan knows: `zip`, `tar`, `gzip`, `bzip2`, `xz`, or `opaque`."""
    if data.startswith(OPAQUE):
        return "opaque"
    if data[:2] == b"\x1f\x8b":
        return "gzip"
    if data[:3] == b"BZh" and data[4:10] == b"1AY&SY":
        return "bzip2"
    if data[:6] == b"\xfd7zXZ\x00":
        return "xz"
    if data[257:262] == b"ustar":
        return "tar"
    if zipfile.is_zipfile(io.BytesIO(data)):
        return "zip"
    return None


def redact_blob(data: bytes, values: set[str], depth: int = 0, where: str = "") -> tuple[bytes, int, list[str]]:
    """Bytes with every key redacted, how many there were, and where each was.

    A place is a member's path, with `!` between an archive and a member
    inside it; an empty place is the bytes themselves. A file the scan
    cannot read becomes `UNREADABLE`, and its place says why.
    """
    kind = form(data)
    count, places = 0, []
    if kind == "opaque":
        return UNREADABLE, 1, [f"{where}(not scanned: a compressed form the scan cannot read)"]
    if kind is not None:
        try:
            if depth >= NESTING:
                raise Unreadable(f"inside {depth} others")
            data, count, places = UNPACK[kind](data, values, depth, where)
        except Unreadable as exc:
            return UNREADABLE, 1, [f"{where}(not scanned: {exc})"]
        except READ_ERRORS as exc:
            if kind != "zip":
                return UNREADABLE, 1, [f"{where}(not scanned: a {kind} stream that does not read: {type(exc).__name__})"]
            # It only looks like a zip; its bytes are scanned below.
    clean, raw = redact_bytes(data, values)
    if raw:
        places.append(where.rstrip("!") + ("(its bytes as they stand)" if kind else ""))
    return clean, count + raw, places


def _zip(data: bytes, values: set[str], depth: int, where: str) -> tuple[bytes, int, list[str]]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        if sum(info.file_size for info in zf.infolist()) > UNPACKED:
            raise Unreadable(f"it unpacks to more than {UNPACKED} bytes")
        comment, count = redact_bytes(zf.comment, values)
        places = [f"{where}(the zip's comment)"] if count else []
        members = []
        for info in zf.infolist():
            name, named = _text(info.filename, values)
            if named:
                places.append(f"{where}{info.filename}")
            try:
                content, found, inside = redact_blob(zf.read(info), values, depth + 1, f"{where}{info.filename}!")
            except READ_ERRORS as exc:
                content, found, inside = UNREADABLE, 1, [f"{where}{info.filename}(not scanned: {exc})"]
            count += found + named
            places += inside
            members.append((info, name, content))
    if not count:
        return data, 0, []
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        zf.comment = comment
        for info, name, content in members:
            copy = zipfile.ZipInfo(name, date_time=info.date_time)
            # A member that did not read was compressed in a way this cannot write; it is stored.
            copy.compress_type = info.compress_type if info.compress_type in WRITABLE else zipfile.ZIP_STORED
            copy.external_attr = info.external_attr
            copy.create_system = info.create_system
            zf.writestr(copy, content)
    return out.getvalue(), count, places


def _tar(data: bytes, values: set[str], depth: int, where: str) -> tuple[bytes, int, list[str]]:
    count, places, members = 0, [], []
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tf:
        infos = tf.getmembers()
        if sum(info.size for info in infos) > UNPACKED:
            raise Unreadable(f"it unpacks to more than {UNPACKED} bytes")
        for info in infos:
            name, named = _text(info.name, values)
            if named:
                places.append(f"{where}{info.name}")
            content = None
            if info.isfile():
                handle = tf.extractfile(info)
                content, found, inside = redact_blob(handle.read() if handle else b"", values, depth + 1, f"{where}{info.name}!")
                count += found
                places += inside
            count += named
            members.append((info, name, content))
    if not count:
        return data, 0, []
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:") as tf:
        for info, name, content in members:
            info.name = name
            if content is not None:
                info.size = len(content)
                tf.addfile(info, io.BytesIO(content))
            else:
                tf.addfile(info)
    return out.getvalue(), count, places


def _unpack_stream(data: bytes, make: Any) -> bytes:
    """Every stream of a gzip, bzip2, or xz file, one after another, unpacked; Unreadable past `UNPACKED` bytes."""
    out, rest = bytearray(), data
    while rest:
        decoder = make()
        chunk = decoder.decompress(rest, UNPACKED + 1 - len(out))
        out += chunk
        while not decoder.eof and len(out) <= UNPACKED:
            more = decoder.decompress(getattr(decoder, "unconsumed_tail", b""), UNPACKED + 1 - len(out))
            if not more:
                break
            out += more
        if len(out) > UNPACKED:
            raise Unreadable(f"it unpacks to more than {UNPACKED} bytes")
        if not decoder.eof:
            raise EOFError("the stream ends early")
        rest = decoder.unused_data
    return bytes(out)


STREAMS = {
    "gzip": (lambda: zlib.decompressobj(31), lambda b: gzip.compress(b, mtime=0)),
    "bzip2": (bz2.BZ2Decompressor, bz2.compress),
    "xz": (lzma.LZMADecompressor, lambda b: lzma.compress(b, format=lzma.FORMAT_XZ)),
}


def _stream(kind: str) -> Any:
    unpack, pack = STREAMS[kind]

    def run(data: bytes, values: set[str], depth: int, where: str) -> tuple[bytes, int, list[str]]:
        inner, count, places = redact_blob(_unpack_stream(data, unpack), values, depth + 1, where)
        return (pack(inner) if count else data), count, places

    return run


UNPACK = {"zip": _zip, "tar": _tar, "gzip": _stream("gzip"), "bzip2": _stream("bzip2"), "xz": _stream("xz")}


def redact_file(path: str | Path, values: set[str]) -> tuple[int, list[str]]:
    """Redact a file in place, as `redact_blob` does; return how many keys it held and where."""
    path = Path(path)
    clean, count, places = redact_blob(path.read_bytes(), values)
    if count:
        temporary = path.with_name(f".{path.name}.redacting")
        temporary.write_bytes(clean)
        temporary.replace(path)
    return count, places


def keys_in(data: bytes) -> list[str]:
    """Where bytes hold a string shaped like a key, or a part the scan cannot read, as `redact_blob` names them."""
    return redact_blob(data, set())[2]


def redact_folder(folder: str | Path, values: set[str], failed: dict[Path, str] | None = None) -> dict[Path, int]:
    """Redact every file under the folder in place; return the files changed and how many keys each held.

    A symlink is not followed: it could point out of the folder, and the
    upload does not follow it either. A zip that changed has its manifest
    and its record written again. A file that cannot be read or written
    is named in `failed` with the reason, and the rest are redacted still;
    with no `failed` given, the error is raised.
    """
    found: dict[Path, int] = {}
    for path in sorted(Path(folder).rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            count, _ = redact_file(path, values)
            if count and path.name.endswith(".zip"):
                A.refresh(path)
        except (*READ_ERRORS, MemoryError) as exc:
            if failed is None:
                raise
            failed[path] = f"{type(exc).__name__}: {exc}"
            continue
        if count:
            found[path] = count
    return found
