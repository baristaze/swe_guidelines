"""The naming rule of the wire types, held over the emitted document: a view
that carries a freshly minted secret (a token, a key, a ticket, a TOTP
secret) is an
`Issued...View`, so the one-time nature is in the name a client reads."""

from pathlib import Path

from api_support import build_container

from acme.services.api.app import create_app

SECRET_FIELDS = frozenset({"token", "key", "ticket", "otpauth_uri"})


def test_a_view_that_carries_a_minted_secret_is_named_issued(tmp_path: Path) -> None:
    document = create_app(build_container(tmp_path)).openapi()
    schemas = document["components"]["schemas"]
    carrying = sorted(
        name
        for name, schema in schemas.items()
        if name.endswith("View") and SECRET_FIELDS & set(schema.get("properties", {}))
    )
    assert carrying == [
        "IssuedApiKeyView",
        "IssuedLoginView",
        "IssuedOperatorTokenView",
        "IssuedSessionView",
        "IssuedTicketView",
        "IssuedTotpSecretView",
    ]
    assert all(name.startswith("Issued") for name in carrying)
    ticket = document["paths"]["/v1/realtime/tickets"]["post"]["responses"]["201"]
    assert ticket["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/IssuedTicketView"
    }
