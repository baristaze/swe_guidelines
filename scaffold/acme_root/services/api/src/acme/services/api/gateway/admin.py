"""The operator gate: the identity stage the bearer reaches is admitted only
when the identity is an operator, and produces an OperatorContext. Two
credentials reach it: the person's own sign-in, which admits only with a
verified second factor and then only to mint one operator token, and an
operator token, which carries one permission and is what every read and
write on the plane takes. The tenancy manager's `admit_operator` decides;
the gate adds the rules of the edge that name the refusal: an operator with
no second factor enrolled yet reaches the two enrolment routes, a sign-in
with its code reaches the mint, and each is refused on every other route,
by name. No OpContext exists on this path."""

from typing import Annotated

from fastapi import Depends, Request

from acme.om.exceptions import OperatorTokenRequired, SecondFactorNotEnrolled
from acme.om.opcontext import OperatorContext, OperatorPermission
from acme.services.api.gateway.auth import Identity
from acme.services.api.gateway.resolve import container_of


async def enrolling_admin(request: Request, identity: Identity) -> OperatorContext:
    """Any admitted operator, an unenrolled one included: for the two routes
    that enrol the second factor."""
    tenancy = container_of(request).managers.tenancy
    return await tenancy.admit_operator(identity)


EnrollingOperatorCtx = Annotated[OperatorContext, Depends(enrolling_admin)]


async def minting_admin(admin: EnrollingOperatorCtx) -> OperatorContext:
    """An operator whose second factor is enrolled: the mint's gate. The
    manager then takes only a sign-in that verified a code, so a token never
    mints a token."""
    if admin.permissions <= {OperatorPermission.ENROL}:
        raise SecondFactorNotEnrolled(
            "enrol a second factor first: POST /v1/admin/me/totp, then /confirm"
        )
    return admin


MintingOperatorCtx = Annotated[OperatorContext, Depends(minting_admin)]


async def current_admin(admin: MintingOperatorCtx) -> OperatorContext:
    """An operator the plane admits to its work: an operator token, whose
    admission carries a read or a write."""
    if admin.permissions <= {OperatorPermission.MINT}:
        raise OperatorTokenRequired(
            "a sign-in mints an operator token and does nothing else: "
            "POST /v1/admin/me/tokens, then present the token"
        )
    return admin


OperatorCtx = Annotated[OperatorContext, Depends(current_admin)]
