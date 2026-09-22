"""The judge providers as one bit-flag.

A selection is a number or a list of names: `3` and `anthropic,openai`
are the same selection. The flag values are part of the command line, so
they never change: a number written in a note last month still means the
same providers today.

A provider is available when its key is in the environment. An
unavailable provider is skipped and named in the results; only
`--strict` turns that into a failure.
"""

from __future__ import annotations

import os
from enum import IntFlag


class Provider(IntFlag):
    """One bit per judge provider."""

    ANTHROPIC = 1
    OPENAI = 2
    GEMINI = 4
    XAI = 8


ALL = Provider.ANTHROPIC | Provider.OPENAI | Provider.GEMINI | Provider.XAI

# Every key name a provider accepts, most preferred first. Gemini takes
# GEMINI_API_KEY only: GOOGLE_API_KEY names a different credential on many
# machines, and reading it silently is how a run judges with the wrong account.
KEY_NAMES: dict[Provider, tuple[str, ...]] = {
    Provider.ANTHROPIC: ("ANTHROPIC_API_KEY",),
    Provider.OPENAI: ("OPENAI_API_KEY",),
    Provider.GEMINI: ("GEMINI_API_KEY",),
    Provider.XAI: ("XAI_API_KEY", "GROK_API_KEY"),
}
# Every name a provider key goes by on a machine, the ambient Google name
# included: what a subject's environment never carries.
JUDGE_KEY_NAMES: tuple[str, ...] = (*(n for names in KEY_NAMES.values() for n in names), "GOOGLE_API_KEY")


def name(provider: Provider) -> str:
    """The lowercase name used in files, flags, and results.

    Raises ValueError on a selection that is not exactly one provider.
    """
    if provider not in members(ALL) or provider.name is None:
        raise ValueError(f"{int(provider)} is not a single provider")
    return provider.name.lower()


def members(flags: Provider) -> list[Provider]:
    """The single providers a selection holds, in flag order."""
    return [p for p in Provider if p & flags]


def parse(text: str | int | None) -> Provider:
    """Read a selection: a number, a comma-joined list of names, or `all`.

    Raises ValueError on an unknown name or a bit no provider owns.
    """
    if text is None or text == "":
        return ALL
    if isinstance(text, int):
        return _from_int(text)
    raw = str(text).strip()
    if raw.lstrip("+-").isdigit():
        return _from_int(int(raw))
    if raw.lower() == "all":
        return ALL
    out = Provider(0)
    for part in raw.split(","):
        key = part.strip().upper()
        if not key:
            continue
        if key not in Provider.__members__:
            known = ", ".join(name(p) for p in Provider)
            raise ValueError(f"unknown provider {part.strip()!r}; known: {known}")
        out |= Provider[key]
    if not out:
        raise ValueError(f"no provider in {text!r}")
    return out


def _from_int(value: int) -> Provider:
    if value <= 0:
        raise ValueError(f"provider selection must be positive, got {value}")
    if value & ~int(ALL):
        raise ValueError(f"provider selection {value} sets a bit no provider owns (max {int(ALL)})")
    return Provider(value)


def key(provider: Provider, env: dict[str, str] | None = None) -> str | None:
    """The key of a provider, or None when the environment has none."""
    source = os.environ if env is None else env
    for var in KEY_NAMES[provider]:
        value = source.get(var)
        if value:
            return value
    return None


def available(provider: Provider, env: dict[str, str] | None = None) -> bool:
    """Whether the environment holds a key for the provider."""
    return key(provider, env) is not None


def availability(flags: Provider = ALL, env: dict[str, str] | None = None) -> list[tuple[str, bool, str]]:
    """One row per provider in the selection: name, key present, key names."""
    return [(name(p), available(p, env), " or ".join(KEY_NAMES[p])) for p in members(flags)]
