"""No key leaves a run folder: every file is scanned and redacted in place.

A run folder holds what a subject printed and wrote, and a subject can
print anything it can read. So before a run folder is shown or uploaded,
every file in it is scanned as bytes, frames included, and two things
are replaced with `[redacted]`: the value of every provider key the
harness knows by name, and anything shaped like a provider key, whether
the harness holds that key or not.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from . import providers as P
from .runtime import SUBJECT_KEYS

REDACTED = b"[redacted]"
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


def redact_folder(folder: str | Path, values: set[str]) -> dict[Path, int]:
    """Redact every file under the folder in place; return the files changed and how many keys each held.

    A symlink is not followed: it could point out of the folder, and the
    upload does not follow it either.
    """
    found: dict[Path, int] = {}
    for path in sorted(Path(folder).rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        data = path.read_bytes()
        clean, count = redact_bytes(data, values)
        if count:
            path.write_bytes(clean)
            found[path] = count
    return found
