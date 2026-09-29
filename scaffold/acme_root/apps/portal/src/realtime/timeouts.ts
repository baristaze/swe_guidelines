// The ping interval and the load balancer idle timeout come from the one
// shared file; a client test and a service test both assert against it.
import pinned from "../../../../deployment/realtime-timeouts.json";

export const PING_INTERVAL_MS = pinned.ping_interval_seconds * 1000;
export const LOAD_BALANCER_IDLE_TIMEOUT_MS = pinned.load_balancer_idle_timeout_seconds * 1000;
export const DEGRADED_POLL_INTERVAL_MS = 30_000;
export const BACKOFF_BASE_MS = 1_000;
export const BACKOFF_MAX_MS = 30_000;
// A socket that has said hello, or stayed open this long, counts as
// connected; only then does the reconnect backoff start over.
export const STABLE_OPEN_MS = 5_000;
// A tab hidden this long closes its socket until it is looked at again. An
// open socket costs the server about 72 transactions an hour (the credential
// recheck and the pings, about 216 round trips), and a reconnect about 5. Five
// minutes open is about 6, so a hide longer than this pays for its return.
export const HIDDEN_PAUSE_MS = 5 * 60_000;

/**
 * The wait before reconnect `attempt` (1 is the first): the base doubled per
 * attempt and capped, then halved and topped up from `random`. A socket drops
 * for a shared reason, so every tab is dropped at once and a bare curve brings
 * them all back at the same instant; half the window is jitter, so they do
 * not. Half of it is fixed, which keeps the shortest wait of one attempt at
 * the longest wait of the one before it wherever the curve doubles, so the
 * growth is still assertable under randomness. The curve and the cap are what
 * they were: the pinned numbers are the top of each window.
 */
export function backoffDelay(attempt: number, random: () => number = Math.random): number {
  const full = Math.min(BACKOFF_BASE_MS * 2 ** Math.max(0, attempt - 1), BACKOFF_MAX_MS);
  return full / 2 + (full / 2) * random();
}
