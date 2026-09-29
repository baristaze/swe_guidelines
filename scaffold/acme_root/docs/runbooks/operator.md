# Becoming an operator

The operator plane reads every tenant, and writes them for a `write`
entry. Getting onto it takes four steps, in order: an identity, a grant,
a second factor, and a token. A person does all four. An agent does none,
and works from the token the last step writes
([ADR 0018](../adr/0018-the-operator-allowlist-carries-a-role.md)).

Staging is below. Production is `https://api.acme.example`, and its
grant runs on `release` behind the deploy's reviewer.

```bash
API=https://api.staging.acme.example
```

## Sign in

Open the environment's portal and sign in through the identity provider
with the address you will operate with. A first sign-in is the sign-up:
it makes your identity and your personal org.

## The grant

```bash
gh workflow run grant-operator.yml --ref main -f environment=staging \
  -f email=<your email> -f permission=read          # or write
```

`read` is enough to investigate. `write` changes a tenant's rows, and a
person uses it for one named step, such as `acme-ops work requeue`.

## The second factor

Until you enrol, the plane admits the enrolment and nothing else. A
terminal signs in with the device sign-in: it prints a code and an
address, and you confirm the code in your browser.

```bash
json() { python3 -c "import json,sys; print(json.load(sys.stdin).get('$1', ''))"; }
sign_in() {
  d=$(curl -s -X POST "$API/v1/auth/device")
  printf 'open %s and confirm %s\n' "$(echo "$d" | json verification_uri_complete)" \
    "$(echo "$d" | json user_code)" >&2
  device=$(echo "$d" | json device_code)
  while :; do
    sleep 5
    t=$(curl -s "$API/v1/auth/device/token" -H 'Content-Type: application/json' \
      -d "{\"device_code\":\"$device\"}" | json token)
    if [ -n "$t" ]; then echo "$t"; return; fi
  done
}
TOKEN=$(sign_in)
curl -s -X POST "$API/v1/admin/me/totp" -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

The answer carries `otpauth_uri`, once. Add it to an authenticator app,
then confirm with its first code:

```bash
printf 'code: '; read -r CODE
curl -s -X POST "$API/v1/admin/me/totp/confirm" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d "{\"totp_code\":\"$CODE\"}"; echo
```

It answers `identity_id` and `confirmed_at`.

## Check the fence, once

While the sign-in still stands (ten minutes):

```bash
curl -s -o /dev/null -w 'no code: %{http_code}\n' "$API/v1/admin/orgs" -H "Authorization: Bearer $TOKEN"
printf 'code: '; read -r CODE
VERIFIED=$(curl -s "$API/v1/auth/second-factor" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d "{\"totp_code\":\"$CODE\"}" | json token)
curl -s -o /dev/null -w 'with code: %{http_code}\n' "$API/v1/admin/orgs" -H "Authorization: Bearer $VERIFIED"
curl -s -o /dev/null -w 'signed out: %{http_code}\n' -X POST "$API/v1/auth/logout" -H "Authorization: Bearer $VERIFIED"
unset TOKEN VERIFIED CODE
```

Expect `401` without a code, then `403 operator_token_required` with
one: a sign-in with its code mints a token and reads nothing itself. The
last line ends that sign-in.

## The token

```bash
uv run acme-ops token --env staging --identity operator
```

It asks you to confirm a sign-in in the browser and for a code, writes
`ACME_OPERATOR_TOKEN` into `~/.config/acme/ops/staging.env` (mode 600),
and prints the token's id. The mint ends the sign-in, so the token is
what remains. It lasts an hour; run the command again when it runs out.
Every ops skill reads that file.

## Ending tokens

One token, now ([ADR 0068](../adr/0068-an-operator-credential-ends-by-itself.md)):

```bash
uv run acme-ops token --env staging --list
uv run acme-ops token --env staging --revoke <id>
```

Both run under the file's token. You end your own tokens only; another
operator's answer `404`. The sign-out ends the token it is given, which
is how a machine identity's token ends early:

```bash
curl -s -X POST "$API/v1/auth/logout" -H "Authorization: Bearer <the token to end>"
```

All of an operator's credentials, at once:

```bash
gh workflow run grant-operator.yml --ref main -f environment=staging \
  -f email=<email> -f permission=none -f disable=true
```

The entry goes, and every operator token and every sign-in with a code
the identity holds ends in the same commit. A later grant revives none of
them. The identity keeps its tenant account.

## When it fails

- **`401` on every plane call.** The token ran out or was revoked. Write
  a fresh one, as The token says.
- **`403 second_factor_not_enrolled`.** Enrol first, as The second factor says.
- **`403` on a write.** The entry is `read`. A `write` step is a grant
  of its own.
