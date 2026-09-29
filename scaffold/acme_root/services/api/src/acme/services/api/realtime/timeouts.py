"""The keep-alive numbers, pinned with the load balancer idle timeout in
deployment/realtime-timeouts.json; `tests/test_realtime_timeouts.py` holds
each one to that file.

Two pings keep a socket alive, one per direction. The client sends an
application ping every `PING_INTERVAL_SECONDS` from its own timer; the
answer, a pong, carries the tenant's head seq. The server sends a protocol
ping every `SERVER_PING_INTERVAL_SECONDS` and closes the socket when no
pong arrives within `SERVER_PING_TIMEOUT_SECONDS`; that pong is answered
by the client's socket implementation (a browser answers it whatever the
tab's timers do), so a peer that is gone is found without the client's
help. The handler also closes a socket that has sent no application frame
for `IDLE_TIMEOUT_SECONDS`, three client pings: a background tab whose
timers are throttled past that is closed and reconnects when it wakes. The
interval plus the timeout stays below the load balancer's idle timeout, so
the server, not the load balancer, is what ends a dead socket."""

PING_INTERVAL_SECONDS = 25
"""How often a client pings; the hello frame names it."""

IDLE_TIMEOUT_SECONDS = PING_INTERVAL_SECONDS * 3
"""A socket that has sent nothing for this long is closed by the handler."""

SERVER_PING_INTERVAL_SECONDS = 20
"""uvicorn's `ws_ping_interval`: the protocol ping the server sends."""

SERVER_PING_TIMEOUT_SECONDS = 20
"""uvicorn's `ws_ping_timeout`: the wait for the pong before the server closes."""
