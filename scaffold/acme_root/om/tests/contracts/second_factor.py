"""The second factor, for tests: a fixed TOTP key, a clock that moves one
time step each time a code is drawn, so no code is ever reused by accident,
and the enrolment an operator goes through before the plane admits them."""

import base64
from datetime import datetime
from urllib.parse import parse_qs, urlparse

from acme.om.base import new_id, utcnow
from acme.om.opcontext import AppContext, AppType, OperatorContext, RequestContext
from acme.om.tenancy.impl.manager import TenancyManagerImpl
from acme.om.tenancy.impl.operator import TenancyOperatorManagerImpl
from acme.om.tenancy.rules import TOTP_STEP, totp_code, totp_step
from acme.om.tenancy.types.issued import IssuedLogin

TOTP_KEY = "YWNtZS10ZXN0cy10b3RwLWtleS10aGlydHktdHdvISE="
"""A Fernet-shaped key (URL-safe base64 of 32 bytes), for tests only."""


class SteppingClock:
    """The managers' TOTP clock in a test. `code` moves it one step and
    answers the code of that step, as an authenticator would a moment later."""

    def __init__(self) -> None:
        self.now = utcnow()

    def __call__(self) -> datetime:
        return self.now

    def code(self, secret: bytes) -> str:
        self.now += TOTP_STEP
        return totp_code(secret, totp_step(self.now))


def secret_of(otpauth_uri: str) -> bytes:
    """The secret an authenticator app reads out of an `otpauth://` URI."""
    encoded = parse_qs(urlparse(otpauth_uri).query)["secret"][0]
    return base64.b32decode(encoded + "=" * (-len(encoded) % 8))


def operator_request() -> RequestContext:
    """The request stage of an operator's call, from the command line, as an
    agent's or a skill's is."""
    return RequestContext(request_id=new_id(), app=AppContext(type=AppType.CLI, version="cli@test"))


async def enrolled_operator(
    manager: TenancyManagerImpl,
    operator: TenancyOperatorManagerImpl,
    clock: SteppingClock,
    email: str,
) -> tuple[OperatorContext, bytes]:
    """An allowlisted identity through its first sign-in to the plane: admitted
    to enrol, a secret minted and confirmed with a first code, then signed in
    again with a second code, a token minted with the whole of its entry, and
    admitted on that token. Returns that stage and the secret, for a test
    that signs in again."""
    login = await manager.dev_sign_in(operator_request(), email)
    enrolling = await manager.admit_operator(
        await manager.authenticate_login(operator_request(), login.token)
    )
    secret = secret_of((await operator.enrol_totp(enrolling)).otpauth_uri)
    await operator.confirm_totp(enrolling, clock.code(secret))
    return await signed_in_operator(manager, operator, clock, email, secret), secret


async def minting_operator(
    manager: TenancyManagerImpl, clock: SteppingClock, email: str, secret: bytes
) -> OperatorContext:
    """An enrolled operator signing in, then verifying a fresh code: the stage
    that mints one token and does nothing else."""
    login = await second_factor(manager, email, clock.code(secret))
    return await manager.admit_operator(
        await manager.authenticate_login(operator_request(), login.token)
    )


async def signed_in_operator(
    manager: TenancyManagerImpl,
    operator: TenancyOperatorManagerImpl,
    clock: SteppingClock,
    email: str,
    secret: bytes,
) -> OperatorContext:
    """An enrolled operator signing in with a fresh code, then presenting the
    token that sign-in mints with the whole of the entry: the stage the plane
    reads and writes with."""
    minting = await minting_operator(manager, clock, email, secret)
    assert minting.operator_entry is not None, "an admitted operator has an entry"
    issued = await operator.issue_operator_token(minting, minting.operator_entry)
    return await manager.admit_operator(
        await manager.authenticate_login(operator_request(), issued.token)
    )


async def second_factor(manager: TenancyManagerImpl, email: str, code: str) -> IssuedLogin:
    """A sign-in by address, then its second factor: the login the operator
    gate admits an enrolled operator on."""
    login = await manager.dev_sign_in(operator_request(), email)
    return await manager.verify_second_factor(
        await manager.authenticate_login(operator_request(), login.token), code
    )
