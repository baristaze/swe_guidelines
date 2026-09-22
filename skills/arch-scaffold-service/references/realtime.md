# arch-scaffold-service: the realtime channel

Step 2 of `arch-scaffold-service` reads this file. It is the
file-by-file list of the `realtime/` package, the replay route, and
their tests, all of them written only with `--realtime`, under
`services/<service-name>/` unless a row says otherwise. The conventions
in `../_shared/scaffold-conventions.md` hold throughout, and what must
never be missed is in the skill's `Created` section, not here.

## Files

| File                                          | Holds                                                                                          |
|-----------------------------------------------|------------------------------------------------------------------------------------------------|
| `src/<root>/services/<svc>/realtime/` (with `--realtime`) | `ticket.py` (`POST /v1/realtime/tickets` under `Ctx` with the `Idempotency-Key` dependency, answering `201` with `ticket`, the route that asks the tenancy manager's `issue_socket_ticket(ctx)` for the ticket; redemption is the manager's, atomic, and re-checks the credential named by `ctx.security.credential_id`), `socket.py` (the websocket route `/v1/realtime/socket?ticket=<ticket>`, resolving its context through the gateway's `socket_context` dependency and never parsing the query itself, then, on open, the subscription to the socket's tenant stream, with nothing for the client to choose: the process routes each `ENTITY_CHANGED` message by its `org_id` to the sockets of that tenant, and the `hello` frame carrying the tenant's head `seq`, read through the events manager's `get_head(ctx)` over `read_head(org_id)`; inbound, `subscribe` and `unsubscribe`, which name visibility scopes only when the product keeps more than one stream and never a kind, and `ping`, answered by a `pong` carrying the head `seq`; the socket is bounded by its session's expiry, a deadline set when the ticket is redeemed that closes it at that instant whatever the client does, and it is closed on the `SESSION_REVOKED` message whose `target_id` is its session, `ctx.security.credential_id`, which every process holding sockets reads from the `ENTITY_CHANGED` topic and never forwards as a frame; the expiry covers a frame that was missed), `send_buffer.py` (the per-socket send buffer and drainer, bounded in two lanes: control frames first, a full buffer dropping the oldest stream frame and never a control frame, the control lane bounded on its own from settings and its overflow logged as such, the revocation close and the transport keepalive staying outside the buffer, as The Network Layer (Realtime at the Edge) states), `envelopes.py` (typed frames on the `View` base, discriminated by `type`: the control frames `hello` and `pong`, each carrying `head_seq`, `subscribed` and `unsubscribed`, and `error`; and the stream frame `event`, a hint carrying the identity of the change, `seq`, `kind`, `target_id`, and `actor_id`, and no field of the entity, since a client reads the entity through the authorized read, as The Network Layer (Realtime at the Edge) states; the portal's `envelopes.ts` mirrors it by hand, since socket frames are not in OpenAPI) |
| `src/<root>/services/<svc>/routers/events.py` (with `--realtime`) | `GET /v1/events?after_seq=&limit=` over the events manager, the replay a reconnecting client uses |

## Tests

| File                                          | Holds                                                                                          |
|-----------------------------------------------|------------------------------------------------------------------------------------------------|
| `tests/test_realtime_timeouts.py` (with `--realtime`) | asserts the ping interval and idle timeout against `deployment/realtime-timeouts.json`, the service half of the shared-file rule |
| `tests/test_socket_close.py` (with `--realtime`) | a socket is closed when its session's expiry passes while the client keeps pinging, and when `SESSION_REVOKED` for its session arrives on the topic bus, after a sign-out, a revoked session, or a removed member; its first frame is `hello` with the tenant's head `seq`, a write in its tenant reaches it with no `subscribe` sent, every `pong` carries the head `seq`, and no `event` frame carries a field of the entity |
