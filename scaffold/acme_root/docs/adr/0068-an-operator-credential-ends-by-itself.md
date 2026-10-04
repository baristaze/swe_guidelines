# ADR 0068: An operator credential ends by itself

**Status**: accepted (2026-09-28)

## Context

Two credentials reach the operator plane: a person's sign-in that
verified a TOTP code, and an operator token. An operator token is a
`sessions` row of kind `operator_token` under the system scope, lives an
hour at most, and carries one permission (CTX-38, "The Gateway").

An operator credential should be short-lived, carry one permission, be
named, and end by itself, one at a time. A leaked token must end before
its hour without disabling its whole operator. A sign-in with a code
must not stand beside the token it minted, with the entry's whole grant
and every tenant the person is in.

## Decision

**A sign-in with its second factor mints one operator token and does
nothing else.** `admit_operator` admits it with `OperatorPermission.MINT`
alone, and the mint caps the token at the entry the stage carries. Every
read and every write on the plane is a token's. The gate names the
refusal, `403 operator_token_required`, as it names
`second_factor_not_enrolled`.

**The token keeps its hour, never minutes**: a skill runs longer than
five minutes, and a person would mint all day. The revoke ends a token
sooner.

**The mint is an exchange.** It lands the token and ends the sign-in in
one write, `exchange_sign_in` into the system scope, as a tenant's
sign-in is exchanged for a session
([ADR 0037](0037-a-sign-in-is-exchanged-once.md)). A second mint with it
is `401`. Like that exchange, the route takes no `Idempotency-Key` and
answers `200`, not `201`: the sign-in is its key, so no retry can land a
second token.

**The second factor ends the sign-in it verified.** `POST
/v1/auth/second-factor` answers a new sign-in and ends the one presented,
in the same write. A wrong code ends nothing. So an operator's run leaves
one live credential: the device sign-in ends at the code, the sign-in
with the code ends at the mint, and the token is what remains.

**An operator lists and revokes their own tokens.** `GET
/v1/admin/me/tokens` pages the caller's live tokens, newest first, the
grant job's for that identity among them, and never a secret. `DELETE
/v1/admin/me/tokens/{id}` stamps `revoked_at` and `updated_by`, as
`revoke_session` does, and logs the operator, the token, and the
credential that ended it. The next request with that token is `401
operator token revoked`, and a second revoke answers the first one's
row. Both take `OperatorPermission.READ`, which every token carries, so
a `read` token ends its siblings and itself. The mint answers the
token's `id`, which `acme-ops token --list` and `--revoke <id>` use.

**Another operator's token is `404`.** The guideline's roles give no
operator the others' credentials. The allowlist is the grant job's alone
([ADR 0018](0018-the-operator-allowlist-carries-a-role.md)), and so is
ending another identity's credentials: its disable. A `write` entry
writes tenants' rows, not operators'.

**Sign-out ends the credential presented, whichever it is**, as the
guideline's `sign_out(ictx)` ends "the credential the identity stage
came from" ("The Operator Context"). `POST /v1/auth/logout` takes the
identity stage. A session ends and is announced under its tenant. A
sign-in ends, with or without its code: a tenant's at the picker, an
operator's before its mint. An operator token ends: that is the
operator's sign-out, and `acme-ops work requeue` signs its `write` token
out once its one call is made. An API key never proves an identity, so
it has no sign-out: `401`.

**Disabling an operator ends its credentials.** The grant job's disable
clears the entry and, in the same commit, ends every live operator token
and every sign-in with a code the identity holds. A grant made again
revives none of them. The person's sessions and plain sign-ins are
theirs, not the operator's, and stand.

No operator credential holds a socket: `TICKET_CREDENTIALS` is the
session and the API key. So an operator's revocation needs no bus
message and no recheck, since every request reads the row.

## Consequences

- The guideline admits "the person's own sign-in, and an operator token"
  to the plane. Acme admits both, and the sign-in to one route. That is
  narrower than the text and breaks no rule, so it is a choice, not a
  deviation.
- The traffic generator and the smoke test present the grant job's
  tokens, and `acme-ops token` and `work requeue` mint one token per
  sign-in. The operator runbook's check of the fence reads the refusal a
  sign-in gets.
- A client that lost the mint's answer signs in again, and revokes the
  lost token by its id from the list.
- An operator who needs a `read` token and a `write` one signs in twice,
  as ADR 0037 says of an operator who needs the plane and a tenant.
- A leaked machine token, the provisioner's or the smoke identity's, is
  ended by presenting it to the sign-out, or for good by the grant job's
  disable. A grant and a mint then issue a fresh one.
