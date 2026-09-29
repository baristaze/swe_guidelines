# ADR 0068: An operator credential ends by itself

**Status**: accepted (2026-09-26). Amends
[ADR 0018](0018-the-operator-allowlist-carries-a-role.md): a sign-in with
a second factor mints one token and does nothing else on the plane. Amends
[ADR 0037](0037-a-sign-in-is-exchanged-once.md): the second factor ends the
sign-in it verified, and the mint of a token is an exchange too.

## Context

Two credentials reach the operator plane, and neither could be ended by
itself.

An operator token lives up to an hour. It is a `sessions` row of kind
`operator_token` under the system scope, and `_check_session` already
refuses a revoked row. But no route set `revoked_at` on one. A leaked
token kept working until it expired, unless the whole operator was
disabled. Disabling was no cure either: it cleared the entry, and a grant
made again within the hour brought every old token back.

A sign-in that verified a TOTP code was admitted with the entry's whole
grant, read and write for a `write` operator, for its 600 seconds. It was
the plane's only multi-use credential with more than one permission. The
sign-out took a session and nothing else, so nobody could end it but its
expiry. The sign-in the code was verified on stood beside it for another
ten minutes, able to enter every tenant the person is in.

The guideline's operator token is short, carries one permission, and is
a session, stored as its digest (CTX-38, "The Gateway"). A session is
revoked one at a time, and a revoked one is refused. The scaffold's
`sign_out(ictx)` ends "the credential the identity stage came from", a
session or not ("ending its own sign-in" is an operation of the identity
stage, "The Operator Context"). So a credential that admits to the
operator plane should be short, carry one permission, be named, and end
by itself. The sign-in with its code met none of the last three.

## Decision

**A sign-in with its second factor mints one operator token and does
nothing else.** `admit_operator` admits it with `OperatorPermission.MINT`
alone. The mint caps the token at the entry the stage carries. Every
read and every write on the plane is a token's. The gate names the
refusal, `403 operator_token_required`, as it names
`second_factor_not_enrolled`.

**The mint is an exchange.** It lands the token and ends the sign-in in
one write, `exchange_sign_in` into the system scope, as a tenant's
sign-in is exchanged for a session (ADR 0037). A second mint with it is
`401`. Like that exchange, the route takes no `Idempotency-Key` and
answers `200`, not `201`: the sign-in is its key, so no retry can land a
second token. The marker it ran under before was a write of the plane,
so a `read` operator's mint with a key was refused `403`; that goes with
it.

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
operator token revoked`. A second revoke answers the first one's row.
Both take `OperatorPermission.READ`, which every token carries, so a
`read` token ends its siblings and itself. The mint answers the token's
`id`, and `acme-ops token --list` and `--revoke <id>` use them.

**Another operator's token is `404`.** The guideline's roles give no
operator the others' credentials. The allowlist is the grant job's alone
(ADR 0018), and so is ending another identity's credentials: its
disable. A `write` entry writes tenants' rows, not operators'.

**Sign-out ends the credential presented, whichever it is.** `POST
/v1/auth/logout` takes the identity stage. A session ends and is
announced under its tenant, as before. A sign-in ends, with or without
its code: a tenant's at the picker, an operator's before its mint. An
operator token ends: that is the operator's sign-out, and `acme-ops
work requeue` signs its `write` token out once its one call is made. An
api key never proves an identity, so it has no sign-out: `401`, where it
was `422`.

**Disabling an operator ends its credentials.** The grant job's disable
clears the entry and, in the same commit, ends every live operator token
and every sign-in with a code the identity holds. A grant made again
revives none of them. The person's sessions and plain sign-ins are
theirs, not the operator's, and stand.

No operator credential holds a socket. `TICKET_CREDENTIALS` is the
session and the api key, so an operator's revocation needs no bus
message and no recheck: every request reads the row.

## Alternatives

- **Keep the sign-in with its code admitting, and let its sign-out end
  it.** Rejected. It would carry the entry's whole grant, read and write,
  for ten minutes. Only its holder could end it, since nothing lists it.
  And a read or a write on the plane would name one of two credential
  kinds. With a token, every act names one listed credential with one
  permission.
- **Shorten the token's life to five minutes.** Rejected. A skill runs
  longer than that, and a person would mint all day. The hour stays; the
  revoke ends it sooner.
- **Let a `write` operator end another operator's tokens.** Rejected.
  That is a write on the allowlist's side of the plane, which only the
  grant job makes, under the deploy's approval.
- **A console screen for the list and the revoke.** Not now. No operator
  console exists (ADR 0010), and the person who mints a token does so in
  a terminal with `acme-ops`, which now lists and revokes too. ADR
  0010's trigger for the console stands.

## Consequences

- The guideline admits "the person's own sign-in, and an operator token"
  to the plane. Acme admits both, and the sign-in to one route. That is
  narrower than the text and breaks no rule, so it is a choice, not a
  deviation.
- Nothing that reaches the plane today used a sign-in to read or write.
  `acme-ops token` and `work requeue` already minted one token per
  sign-in, the traffic generator and the smoke test present the grant
  job's tokens, and no operator console exists. The runbook's check of
  the fence now reads the refusal a sign-in gets.
- A client that lost the mint's answer signs in again, and revokes the
  lost token by its id from the list.
- An operator who needs a `read` token and a `write` one signs in twice,
  as ADR 0037 says of an operator who needs the plane and a tenant.
- A leaked machine token, the provisioner's or the smoke identity's, is
  ended by presenting it to the sign-out, or by the grant job's disable,
  which now ends it for good. A grant and a mint then issue a fresh one.
