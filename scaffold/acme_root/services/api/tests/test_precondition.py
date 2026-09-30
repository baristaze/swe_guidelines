"""The precondition a write carries, over the in-process app: `If-Match`
names one version, as an entity tag or bare, and anything else is refused by
name, whatever bytes the header holds."""

import httpx
import pytest
from fastapi import FastAPI

from acme.om.exceptions import ValidationFailed
from acme.services.api.gateway.precondition import IfMatch, if_match_version

ROUTE = "/precondition"


@pytest.fixture
def versioned(app: FastAPI) -> None:
    """A route that answers with the version its precondition names."""

    @app.put(ROUTE, include_in_schema=False)
    async def precondition(version: IfMatch) -> dict[str, int | None]:
        return {"version": version}


@pytest.mark.usefixtures("versioned")
async def test_a_precondition_names_one_version(client: httpx.AsyncClient) -> None:
    assert (await client.put(ROUTE)).json() == {"version": None}
    for header in ('"3"', "3", ' "3" '):
        named = await client.put(ROUTE, headers={"If-Match": header})
        assert named.status_code == 200, named.text
        assert named.json() == {"version": 3}


@pytest.mark.usefixtures("versioned")
@pytest.mark.parametrize(
    "header",
    [
        b"*",
        b'W/"3"',
        b'"1", "2"',
        b'"0"',
        b'"\xb2"',
        b'"\xb9\xb2"',
        b'"' + b"9" * 5000 + b'"',
    ],
    ids=[
        "a star",
        "a weak tag",
        "a list",
        "no version",
        "a digit of another script",
        "two of them",
        "more digits than a number is read from",
    ],
)
async def test_a_malformed_precondition_is_refused_by_name(
    client: httpx.AsyncClient, header: bytes
) -> None:
    """A header's bytes reach the gateway as text, one character each. A
    character that is a digit and no ASCII digit is no version, and neither
    is a number too long to read: each is the refusal any malformed
    precondition gets, never an exception."""
    refused = await client.put(ROUTE, headers={b"If-Match": header})
    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["code"] == "validation_failed"


def test_a_digit_of_another_script_is_no_version() -> None:
    """Digits `int` would read as a number are refused too: a version is
    written in ASCII digits alone."""
    for header in ('"\uff13"', "\u0663"):
        with pytest.raises(ValidationFailed):
            if_match_version(header)
