// The app's one retry policy, as pure rules the transport client runs.
// Only a failure that can differ on a second attempt is retried, the count is
// bounded, and the delay grows and carries jitter. The query library's own
// retry is off (see src/app/queryClient.ts), so no call is retried twice over.

/** Extra attempts a retryable failure gets after the first one. */
export const DEFAULT_RETRY_ATTEMPTS = 2;
/** The delay before the first extra attempt; it doubles from there. */
export const DEFAULT_RETRY_BASE_DELAY_MS = 250;
/** However far the doubling runs, no wait between attempts is longer. */
export const MAX_RETRY_DELAY_MS = 5_000;

/**
 * The answers that say the server could not serve this call and may serve the
 * next one: the platform's own `unavailable`, and the two a proxy sends when
 * the origin refused the connection or did not answer in time. Every other
 * status is a decision, and a decision does not change because it is asked
 * for again.
 */
const RETRYABLE_STATUSES: ReadonlySet<number> = new Set([502, 503, 504]);

/** Methods with no effect on the server, so a second attempt costs a read. */
const SAFE_METHODS: ReadonlySet<string> = new Set(["GET", "HEAD", "OPTIONS"]);

export function isRetryableStatus(status: number): boolean {
  return RETRYABLE_STATUSES.has(status);
}

/**
 * Whether this request may be sent twice. A safe method may. A creating POST
 * under an idempotency key may, because the gateway records the outcome under
 * the key and replays it, so the second attempt finds the row the first one
 * made instead of making another. Everything else may not: a POST with no key,
 * a PATCH, and a DELETE all carry an effect that a lost answer leaves in doubt,
 * and a duplicate write costs more than the failure the caller is told about.
 */
export function mayRetryRequest(method: string, idempotencyKey?: string): boolean {
  const verb = method.toUpperCase();
  if (SAFE_METHODS.has(verb)) return true;
  return verb === "POST" && !!idempotencyKey;
}

/**
 * The wait before attempt `attempt` (1 is the first retry): the base doubled
 * per attempt and capped, then halved and topped up from `random`. Half the
 * window is fixed and half is jitter, so callers that failed together do not
 * return together, and the shortest wait of one attempt is still the longest
 * wait of the one before it wherever the curve doubles, which is what makes
 * the growth assertable.
 */
export function retryDelayMs(
  attempt: number,
  baseMs: number = DEFAULT_RETRY_BASE_DELAY_MS,
  random: () => number = Math.random,
): number {
  const full = Math.min(baseMs * 2 ** Math.max(0, attempt - 1), MAX_RETRY_DELAY_MS);
  return full / 2 + (full / 2) * random();
}

/**
 * A `Retry-After` in seconds, as the gateway sends it, in milliseconds; an
 * HTTP date or anything unreadable is no ask at all.
 */
export function retryAfterHeaderMs(header: string | null): number | undefined {
  if (header === null || !/^\d+$/.test(header.trim())) return undefined;
  return Number(header.trim()) * 1000;
}

/**
 * The wait before a retry: the curve's, or longer when the server asked for
 * longer, and never past the cap, so an ask for minutes does not hold a
 * screen that long.
 */
export function retryWaitMs(curveMs: number, serverAskedMs?: number): number {
  return Math.min(Math.max(curveMs, serverAskedMs ?? 0), MAX_RETRY_DELAY_MS);
}
