"""The precondition a write carries: `If-Match`, the version of the record the
caller read, as an entity tag (`"3"`). The gateway parses the header, and the
service decides what a missing one means."""

from typing import Annotated

from fastapi import Depends, Header

from acme.om.exceptions import ValidationFailed

IfMatchHeader = Annotated[
    str | None,
    Header(
        alias="If-Match",
        description='The version the caller read, as an entity tag: `"3"`. 412 '
        "`precondition_failed` when the record changed since.",
    ),
]


def if_match_version(if_match: IfMatchHeader = None) -> int | None:
    """The version the header names, quoted as an entity tag or bare; None
    with no header. A weak tag, a list, or a star names no one version, and
    a write compares with exactly one, so each is refused. A version is
    written in ASCII digits alone: `isdigit` admits the digits of every
    script, some of which `int` refuses, and `int` reads a number only up to
    its digit limit."""
    if if_match is None:
        return None
    tag = if_match.strip()
    tag = tag[1:-1] if len(tag) >= 2 and tag[0] == tag[-1] == '"' else tag
    try:
        version = int(tag) if tag.isascii() and tag.isdigit() else 0
    except ValueError:
        version = 0
    if version < 1:
        raise ValidationFailed(f"If-Match names no version: {if_match!r}")
    return version


IfMatch = Annotated[int | None, Depends(if_match_version)]
