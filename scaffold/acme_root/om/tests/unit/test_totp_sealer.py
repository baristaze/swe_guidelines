"""The TOTP secret at rest: sealed under the process's key, bound to its
identity, and refused by a process that holds no key or another one."""

import pytest
from contracts.second_factor import TOTP_KEY

from acme.om.base import new_id
from acme.om.exceptions import Unavailable
from acme.om.tenancy.impl.totp import TotpSealer, new_key, new_totp_secret


def test_a_sealed_secret_opens_for_its_identity_under_its_key_only() -> None:
    sealer = TotpSealer(TOTP_KEY)
    identity_id, secret = new_id(), new_totp_secret()
    sealed = sealer.seal(identity_id, secret)
    assert sealed.startswith("v1.") and secret.hex() not in sealed
    assert sealer.seal(identity_id, secret) != sealed, "a fresh nonce each time"
    assert sealer.open(identity_id, sealed) == secret
    with pytest.raises(Unavailable):
        sealer.open(new_id(), sealed)
    with pytest.raises(Unavailable):
        TotpSealer(new_key()).open(identity_id, sealed)
    with pytest.raises(Unavailable):
        sealer.open(identity_id, "v2." + sealed[3:])


def test_no_key_refuses_and_a_key_of_the_wrong_shape_refuses_to_start() -> None:
    with pytest.raises(Unavailable):
        TotpSealer(None).seal(new_id(), new_totp_secret())
    with pytest.raises(ValueError, match="32 bytes"):
        TotpSealer("not-a-key")
    assert len(new_totp_secret()) == 20
