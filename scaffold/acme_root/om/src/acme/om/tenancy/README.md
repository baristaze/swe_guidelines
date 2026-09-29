# Tenancy

The org, the people in it, and the credentials they hold. This is one
of the seven kinds of thing [Acme is made of](../../../../README.md).

## The nouns

- **Org**: a name, a slug (the short handle that names the org in a
  URL), and a kind. The slug is unique among living orgs. An org is
  personal or team. Every person has exactly one **personal org**, made
  with them: their place to work from the first moment, with a name and
  a slug made for them. Every other org is a **team org**, made on
  purpose. A personal org is otherwise an ordinary org: anyone may be
  added to it, and it holds tasks the way a team org does. An org that
  invites people or sets up single sign-on also has an organization at
  the identity provider, made the first time it is needed.
- **Identity**: one person across every org: an email, the identity
  provider's name for them (an issuer and a subject), and whether the
  person is on the operator allowlist and what an operator may do there
  (read, or write, which includes read), and the person's time zone.
  Acme keeps no password.
- **User**: the identity inside one org: the display name and the
  email the team sees.
- **Membership**: the user's place in the org, with a role.
- **Invitation**: a person asked to join an org, by email, with a
  role. The identity provider sends the email with the link; the
  invitation says who asked, the role, and whether it is pending,
  accepted, or revoked. A link stops working at its expiry.
- **Session**: a signed-in visit. A login is a session with no org
  yet, the one a sign-in produces; exchanging it for an org gives a
  session in that org, and ends the login. A live session also proves
  who the person is, so it can list their orgs and be exchanged for a
  session in another one. A session ends at its absolute lifetime, 30
  days, or after it sits idle for 14, whichever comes first. Only a
  request of the person's moves the idle clock. Only the hash of the
  token is kept.
- **Second factor**: an operator's TOTP secret, sealed under a key the
  process holds, enrolled once it is confirmed by a first code.
- **Operator token**: the credential the operator plane reads and
  writes with, a person's or an agent's. One permission, an hour at most,
  kept as its hash, good for the operator plane alone, and ended by
  itself when its operator revokes it.
- **API key**: a named, expiring credential for a program, with a
  role. Only the hash is kept; the key is shown once.
- **Socket ticket**: a single-use, short-lived pass for the live
  channel, standing for the session or the API key that asked for it.

## What can happen

- **Sign in.** A person signs in at the identity provider (WorkOS
  AuthKit: an email code or link, Google, GitHub, or their org's single
  sign-on) and comes back with a code, which the API exchanges there,
  server-side. The provider vouches for an issuer, a subject, and an
  email it has verified; an address it has not verified is refused. The
  identity is found by the issuer and the subject; else by the email,
  and linked from then on; else it is made. The answer is a login that
  lists the orgs the person belongs to, their personal org among them,
  and the login is exchanged for a session in one of them. A person an
  older release made without a personal org gets it at this sign-in.
- **Sign up.** There is no separate sign-up: a person nobody knows
  signs up by signing in. The identity, their personal org, their user
  in it, and the owner membership land together (`create_person`). The
  personal org is named after the person and its slug is generated
  from that name with a random tail.
- **Sign in from the command line.** A terminal has no browser to come
  back to, so it asks for a device sign-in: the person confirms a short
  code at the provider's address, in any browser, and the terminal,
  asking every few seconds, gets the login.
- **Confirm the second factor.** An operator with a second factor
  presents their login and the code from their authenticator, and gets
  a new login that records it; the operator plane asks for that one. The
  login presented ends in the same step, so a person holds one sign-in
  at a time. A run of wrong codes for one email makes the next one wait,
  and a wrong code ends nothing.
- **Sign in locally.** On a developer's machine and in the tests, a
  person signs in by their address alone, and is made the first time,
  for the seed, the demos, and the traffic generator. A deployed
  environment refuses to start with it on.
- **Create a team org.** A signed-in person names an org, and a slug
  if they want one; otherwise the slug is made from the name. The org,
  their user in it under the name they carry where they are, and the
  owner membership land together, and the answer is their place in it.
  They move there with the switch below. Only a session creates an org;
  an api key is a program's and belongs to its tenant.
- **List my orgs, and switch.** A login or a live session lists the
  orgs the person belongs to, a page at a time. Exchanging a live
  session for another org is a switch: the session presented ends in
  the same step that issues the new one, so a person holds one session
  per tab. A sign-in over a tab or a terminal that still holds a session
  is a new sign-in, not a switch: its exchange presents the sign-in, not
  the session. So the client ends the session it held once it holds the
  new one, with the old session's own token, as a sign-out does (ADR
  0047).
- **Sign out.** The credential presented ends, whichever it is: a
  session, a login (one at the org picker, or an operator's with its
  code), or an operator token. An API key is a program's and has no
  sign-out; its holder revokes it. A session that came
  from a sign-in through the provider's hosted page also names the
  provider's own session in that browser, and the sign-out answers the
  provider's logout address for it: the browser goes there, the
  provider ends its session, and it sends the browser back to the
  portal's page that says so. Without that, the next sign-in on the same
  browser would let the same person straight back in, which a shared
  computer must not. A session from the device sign-in or the local
  sign-in has nothing there to end, and signs out of Acme alone. A
  person can also list their own live sessions in the org and revoke any
  one of them.
- **Read and change the profile.** The org, the person's own identity,
  and their display name. The email belongs to the identity.
- **Invite.** An owner or an admin invites a person by email, with a
  role no higher than their own; the provider sends the email with the
  link. A person who is a member already, or whose address has an open
  invitation, is refused; an expired one is replaced. Signing in
  through the link makes the person a member with that role. Pending
  invitations are listed a page at a time, sent again with a fresh
  expiry, or revoked. This is how a person joins an org in a deployed
  environment, and the one place a limit on an org's members is
  checked.
- **Set up single sign-on.** An owner or an admin of a team org opens
  the identity provider's admin portal from the org's settings and
  connects their company's identity provider there, or proves the org's
  domain. A sign-in through that single sign-on makes the person a
  member when their address is in a domain the org verified; anyone
  else joins by invitation. A personal org has no single sign-on.
- **Manage members.** List the members a page at a time, change a
  member's role, or remove a member. Nobody changes their own role, so
  an org's last owner stays one until they make someone else an owner.
  An owner can make another member an owner; an admin gives roles up
  to admin. Settings has the control on each member. A role change is
  announced, and the member's open screens reconnect under the new role.
  Removing ends the
  membership, hides the user from every list, and revokes their
  sessions and API keys, all in one step, and announces each
  revocation so their sockets close. A failure lands none of it.
- **Manage API keys.** Create one with a name, a role, and a lifetime
  of at most ninety days; list them (a member manager sees the org's,
  everyone else their own); revoke one. An org on a plan without API
  keys is refused the first one, and a key it already has is kept and
  refused while it is on that plan ([billing](../billing/README.md)).
- **Open the live channel.** Issue a socket ticket; redeem it once.
  An open channel asks again, every few minutes, whether the session or
  the key behind it still holds, and closes when it does not. Asking
  is not a use: an open channel never keeps an idle session alive.
- **Seed an environment.** Create an org with its first owner, or add
  a person to an org. Both are the platform's own operations, run by
  `make seed` on a local environment; a deployed one is joined by
  invitation. A person either one makes who did not exist before comes
  with their personal org, in the same commit, and signs in with the
  address.
  The seeding is the platform arranging a laptop, not a tenant adding
  someone, so it is not bound by the org's seats, and it grants the
  seeded team Team. An invitation, and a join through the org's single
  sign-on, take a seat like any other door: an invitation past the seats
  is refused before it is sent, and one accepted when the seats have
  filled meanwhile stays pending while the sign-in goes on.
- **Operate across orgs.** An operator, admitted from the allowlist,
  can create an org with its owner, add a member (bound by the org's
  seats like any other door), read an org, its
  members, its tasks, and its events, read the platform's size (the
  maintenance worker counts it every five minutes and the read answers
  its latest count, with when it counted), list every org, and delete a
  team org. An operator's deletion is an
  owner's: the org closes for everyone in it at once, and the providers
  go before the org does (below). An operator never deletes a personal
  org: it goes only with its person.
  A deleted org keeps its row as the record; everything else of it is
  purged once the retention has passed, and once nothing is left the
  org is marked purged and the sweep stops visiting it.
- **Delete an organization.** An owner deletes their team org from a
  session, by typing its name, in Settings. An admin, a member, and an
  API key cannot. A personal org is never deleted this way: it goes
  with its person's account. One step closes the org for everyone in
  it: every member's place ends, every session, API key, and pending
  invitation in it is revoked, each announced so every socket closes,
  and a sign-in through the org's single sign-on no longer finds it.
  Then the providers go: the org's organization at the identity
  provider, its subscription and customer at the payment processor, and
  its Slack app. Then the org is deleted, and its data is purged once
  the retention has passed. The owner lands in their personal org. An
  operator's deletion takes the same steps
  ([ADR 0042](../../../../../docs/adr/0042-an-owner-deletes-a-team-org-closed-at-once-and-its-providers-by-the-queue.md)).
- **Delete my account.** A person deletes their own account from a
  session, by typing its email. It is refused while they are the last
  owner of a team org, and the refusal names each one: they make
  someone else an owner, or delete the organization, first. It is refused while they are on the
  operator allowlist. Otherwise one commit erases them for good: the
  identity with its second factor, their user and membership in every
  org, every session, api key, sign-in, and operator token they hold,
  the sign-in delay of their address, and every invitation sent to it. Each live credential is
  announced as revoked, so their sockets close. What they made in a
  team org stays the org's, under an id that no longer names anyone,
  and their open tasks there go unassigned. Their personal org goes
  whole, once the providers are done: the person at the identity
  provider, the org's subscription and customer at the payment
  processor, and its Slack app; then the org is deleted, and the next
  sweep purges it, with no retention. It is gone now, and gone from the
  backups within seven days. There is no undo, and the same address
  signs up again as a new person
  ([ADR 0041](../../../../../docs/adr/0041-an-account-is-deleted-at-once-and-its-providers-by-the-queue.md)).
- **Grant an operator.** The grant job puts an identity on the
  allowlist, takes it off, or mints the operator token of the
  provisioner or the smoke identity. Each change of the allowlist is
  audited. Taking an identity off ends every operator token and every
  sign-in with a code it holds, at once, so a grant made again later
  revives none of them. The platform's own identities live in a reserved domain,
  which a sign-in refuses, and the first grant makes them.
- **Enrol a second factor.** An allowlisted person's first sign-in to
  the operator plane reaches two calls and nothing else: mint the
  secret, then confirm it with a first code. From then on the plane
  admits them only on a sign-in that verified a code.
- **Mint, list, and revoke operator tokens.** An operator's sign-in with
  its code mints one operator token and does nothing else: the mint ends
  the sign-in. The operator lists their own live tokens, the grant job's
  for them among them, and revokes any one by its id; it is refused from
  its next request. Revoking one that has ended already answers it as
  it is. Another operator's tokens are not theirs to see or end: the
  grant job's disable ends those
  ([ADR 0068](../../../../../docs/adr/0068-an-operator-credential-ends-by-itself.md)).
- **Record a time zone.** A person's time zone is an IANA name, like
  `Europe/Istanbul`, kept on their identity, so it holds in every org
  they are in. The portal sends the one the browser reports when the
  person signs in. A task's reminder goes out at nine in the morning
  there ([tasks](../tasks/README.md)). Anything that is not such a name,
  an offset like `+03:00` among them, is refused.
- **Sweep.** Removed members, revoked or expired keys, expired
  sessions, closed or expired invitations, and old runs of wrong
  second-factor codes are deleted for good
  after the retention, thirty days by default; a revoked session goes
  once it has expired too, within its thirty days. A socket ticket,
  which lives a minute, is deleted a day after it expired. A deleted
  personal org keeps no retention: its rows go at the next sweep. The
  events of this namespace carry ids and never a value, so a person's
  deletion leaves nothing of them there.

## The rules

- **The role ladder.** Viewer, member, admin, owner, in that order. A
  viewer reads. A member also writes and manages their own keys. An
  admin and an owner also manage members and the org's plan. The owner ranks above the
  admin although they hold the same permissions today, because the
  owner is the founder and not a permission set.
- **Nothing is issued above the issuer.** An API key's role and a
  granted or changed membership's role are capped at the caller's own
  role. The service role, which the platform's own work runs under, is
  no rung of the ladder: nothing a person mints may carry it.
- **A key never mints a key.** Only a session creates an API key, so
  revoking a leaked key ends every access it gave.
- **Unique among the living.** One slug per living org, one personal
  org per identity, one user per identity in an org, one membership per
  user in an org, one identity per subject of an issuer, one pending
  invitation per address in an org. Removing a member or deleting an org frees the name
  for reuse.
- **A personal org stays its person's.** It is deleted only with its
  person, and its
  person is not removed from it and does not change role in it, so it
  never changes hands. Nobody leaves an org by removing themselves, in
  any org. Everyone else in a personal org is an ordinary member.
- **Secrets are fingerprints.** A session token, an API key, and a
  socket ticket are stored as hashes. The plain value is shown once.
- **A ticket works once.** A second redemption is refused.
- **A sign-in is exchanged once.** The exchange ends the login in the
  same write that makes the session, so one sign-in makes one session.
  A second exchange, a replay, or a retry after a lost answer is
  refused, and the person signs in again; of two at once, one lands.
  The exchange takes no idempotency key: the login is the key, and a
  replayed answer could not carry the session's token
  ([ADR 0037](../../../../../docs/adr/0037-a-sign-in-is-exchanged-once.md)).
- **An address is one address in any case.** Acme keeps an address in
  lower case and looks it up that way, so `Dee@example.test` and
  `dee@example.test` are one person, one pending invitation in an org,
  and one run of wrong codes, however the provider, Slack, the seeding,
  or an operator spells it
  ([ADR 0072](../../../../../docs/adr/0072-an-address-is-one-address-in-any-case.md)).
- **A verified address or nothing.** The identity provider's sign-in
  counts only with an address it verified; that is what links a person
  Acme already knows to the provider's name for them.
- **The operator plane reads and writes with a token.** A session
  proves the person, but it is a tenant's credential, and the operator
  plane refuses it. A person's sign-in admits only when it verified a
  TOTP code, and then only to mint one operator token, which ends it.
  Every read and write on the plane presents a token, minted that way or
  by the grant job. A token never mints a token and never enters a
  tenant.
- **A code works once.** A TOTP code accepted once is refused after,
  even inside its thirty seconds.
- **A gone org refuses its logins.** Exchanging a login for an org
  that is deleted, or a membership that has ended, is refused as not
  authorized; the login itself still stands.
- **Another org's things do not exist.** A user or a membership that
  belongs to another org is answered exactly like one that never
  existed.
