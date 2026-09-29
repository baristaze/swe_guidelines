# ADR 0035: Slack is a distributed app over HTTP, installed per org

**Status**: accepted (2026-09-23). The contract half is done
(2026-09-25): the code-linked channel's two tables are dropped, and
`<prefix>slack_bot_token` is gone from every environment.
Amended (2026-09-26): the bot asks for `channels:read` and `groups:read`
too, for `member_joined_channel` alone; see the note at the end.

## Context

Acme talks to Slack both ways. People type `/acme` and mention
`@acme`; Acme posts reminders and task updates in a channel. Slack
documents one shape for an app that many workspaces install: every call
from Slack is an HTTPS request signed with the app's signing secret and
answered within three seconds; each workspace installs the app through
OAuth v2 and hands over a bot token for itself alone; and a distributed
app renews its tokens (token rotation).

The guideline asks the same of any inbound provider. "Queues" asks that a
call from outside be checked at the edge, answered, and queued with a key
derived from the provider's delivery id, and that the handler dedupe on
it. "Secrets" says a secret belongs to a tenant: every call takes the
`org_id` first, and an entity holds only a `credential_ref`, which its
manager sets. "Twins for External Services" asks for a twin a laptop and
the tests run.

This record names the choices that shape leaves to the product.

## Decision

**Slack calls the API over HTTP.** Three routes sit outside `/v1`, beside
the payment processor's: `POST /webhooks/slack/commands`, `POST
/webhooks/slack/events`, and `GET /webhooks/slack/oauth`, where Slack
sends a browser back at the end of an install. A command or an event is
checked with the signing secret over its raw body (`v0:<timestamp>:<body>`,
HMAC-SHA256, compared in constant time by the Slack SDK, within five
minutes), answered at once, and put on the `slack` queue for the worker.
The events route answers Slack's `url_verification` challenge after the
same check. No process holds a socket to Slack.

**The delivery key comes from Slack's own id.** An event's key is a UUID
v5 over its `event_id`, which Slack keeps across its retries; a command's
is over its `trigger_id`, which names one invocation. A task
`/acme add` creates takes an id derived from that key (ADR 0027). The
reply to a mention is recorded under the key. So Slack's retries and the
queue's redeliveries each do one thing.

**Each org installs the app.** An owner or an admin starts the install
from Acme's settings. The API keeps a one-time `state` as its digest,
bound to the org and to the member, for ten minutes, and redeems it in
one conditional write when Slack comes back. The code is traded with
`oauth.v2.access` for the workspace's token. The installation is a row of
the org: the workspace, the bot user, the scopes, the channel, and a
`credential_ref`.

**The bot token is the org's own secret.** The token and its refresh
token are written through `SecretsInterface` under the org
(`org/<org_id>/slack_bot_<installation id>`), and the row holds only
their name, in its `MANAGER_OWNED_FIELDS`. So the serving tasks write
secrets for the first time: the grant is `CreateSecret`, `PutSecretValue`,
and `DeleteSecret` on `<prefix>app/org/*` alone, in the environment's
policy and in the account's task boundary. Nothing else a task holds
changes.

**Token rotation is on.** Slack's security guidance asks a distributed app
to renew its tokens. An access token lives twelve hours, and its refresh
token works once. The slack manager renews a token an hour before it
expires, one renewal at a time: a conditional write on the row's
`refreshing_until` names the one caller that refreshes, and a caller that
finds a renewal running uses the token in hand, which the margin keeps
valid. The new pair is written before it is used.

**One workspace belongs to one org.** A second org's install of a
workspace another org holds is refused, and the token Slack handed over
for it is revoked. An org that installs another workspace moves to it,
and the app leaves the first. An org that installs the same workspace
again (for new scopes, or to mend a broken install) keeps its channel.

**The channel is bound in Slack.** An owner or an admin types `/acme
connect` in the channel, after `/invite @acme`. Acme posts a first line
there and binds the channel only when that line lands. This needs no
`incoming-webhook`, no `channels:read`, and no `chat:write.public`, and the
bot posts only where it was invited.

**A command runs as the person who typed it.** The worker reads the
Slack user's email (`users.info`, with `users:read` and
`users:read.email`) and finds the member of the org whose identity holds
that address, the one their sign-in proved. The command runs with that
member's own role. Someone Acme does not know is told how to join.

**The commands are few.** `/acme` alone is your ten newest open tasks,
the portal's My Tasks: assigned to you, or unassigned and made by you.
`/acme team` is the org's ten newest. Then `/acme add <title>`,
`/acme connect`, and `/acme help`. Editing and deleting stay in Acme.

**Each deployed environment has its own app.** An app has one set of
request URLs. The two manifests are committed in `deployment/slack/`, and
a test holds their scopes, URLs, and events to the code. The client id is
public and committed per environment; the client secret and the signing
secret are process credentials.

## Consequences

The routes follow the payment processor's, and so do two deviations ADR
0031 names for it. The Slack routes carry no rate limit: they take no
credential to key one on, and the signature check is the gate. The
signing secret and the client secret are process credentials injected at
start, not read through `SecretsInterface`: they are the app's, not a
tenant's. The workspace's token is a tenant's, and follows the rule.

The account's task boundary is the bootstrap root's, which no deploy
applies. Until a person applies it, an install fails on `CreateSecret`.
The runbook puts that step before the first install.

A refresh token works once. A process that dies between Slack's answer and
the write of the new pair leaves the org a refresh token Slack revokes
after its grace period. The next renewal is refused, the installation is
marked broken, and the settings page asks for a new install. The window
is one secret write wide.

Token rotation cannot be turned off for an app once it is on.

The channel linked by a one-time code is gone. Its two tables stay for one
release, unread, so the release before this one serves its requests during
the rollout; the release after drops them. Their rows are not carried
over: a code-linked channel holds no token, so each org installs again.

A laptop has no public URL, and Acme adds no tunnel. Locally Slack is the
twin: "Add to Slack" installs into the twin's workspace at once, and
Slack's calls in are signed requests with the twin's secret, which the
tests make too. The real Slack is tried on staging.

## Amended: the bot invited back mends its channel

A channel that refuses a post for good (`not_in_channel`, and
`channel_not_found` for a private channel the bot is not in) marks the
installation broken, and posting stops. Most often someone removed
`@acme` from the channel, and fixes it with `/invite @acme`. Slack
tells an app about that invite with its `member_joined_channel` event,
whose member is the bot. The event comes from a public channel only to
an app that holds `channels:read`, and from a private one only with
`groups:read`.

So the bot asks for both scopes and the manifests subscribe to the
event. The worker takes the event off the `slack` queue like any other.
When the member is the installation's bot and the channel is the bound
one, an installation that channel broke is well again, announced, and
posts resume. Any other join changes nothing. A token Slack refused is
not mended by a join: it still takes a new install.

The rule behind it is push over polling. The other way to learn that the
channel works again is to keep posting into it and see; Slack pushes
the answer instead.

The two scopes also open `conversations.list` and `conversations.info`
to the bot. Acme calls neither, and reads no message. The binding does
not change: the channel is still bound by `/acme connect` typed in it,
and the bot still posts only where it was invited.

A workspace that installed Acme before the change holds the old
scopes, and Slack sends it no join. Its owner or admin clicks **Add to
Slack again**. Until then, `/acme connect` in the channel mends the
installation, as before.
