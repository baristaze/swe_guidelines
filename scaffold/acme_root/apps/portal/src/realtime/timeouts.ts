// The ping interval and the load balancer idle timeout come from the one
// shared file; a client test and a service test both assert against it.
import { RetryPolicy } from "@acme/client";
import pinned from "../../../../deployment/realtime-timeouts.json";

export const PING_INTERVAL_MS = pinned.ping_interval_seconds * 1000;
export const LOAD_BALANCER_IDLE_TIMEOUT_MS = pinned.load_balancer_idle_timeout_seconds * 1000;
export const DEGRADED_POLL_INTERVAL_MS = 30_000;
// A socket that has said hello, or stayed open this long, counts as
// connected; only then does the reconnect backoff start over.
export const STABLE_OPEN_MS = 5_000;
// A tab hidden this long closes its socket until it is looked at again. An
// open socket costs the server about 72 transactions an hour (the credential
// recheck and the pings, about 216 round trips), and a reconnect about 5. Five
// minutes open is about 6, so a hide longer than this pays for its return.
export const HIDDEN_PAUSE_MS = 5 * 60_000;

/**
 * The wait before each reconnect: 1s doubled per failed try up to 30s, the
 * top of each window. A socket drops for a shared reason, so every tab is
 * dropped at once and a bare curve brings them all back at the same instant;
 * half of each wait is drawn, so they do not.
 */
export const RECONNECT_POLICY = new RetryPolicy({ baseMs: 1_000, capMs: 30_000, jitter: 0.5 });
