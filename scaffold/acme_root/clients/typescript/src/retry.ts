// The app's one retry policy, as pure rules the transport client runs.
// Only a failure that can differ on a second attempt is retried, the count is
// bounded, and the delay grows and carries jitter. An app turns its query
// library's own retry off (the portal's src/app/queryClient.ts), so no call
// is retried twice over.

/** Extra attempts a retryable failure gets after the first one. */
export const DEFAULT_RETRY_ATTEMPTS = 2;
/** The delay before the first extra attempt; it doubles from there. */
export const DEFAULT_RETRY_BASE_DELAY_MS = 250;
/** However far the doubling runs, no wait between attempts is longer. */
export const MAX_RETRY_DELAY_MS = 5_000;

/**
 * The answers that say the server could not serve this call and may serve the
 * next one: the system's own `unavailable`, and the two a proxy sends when
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

/** What a `RetryPolicy` is made of; `growth` defaults to 2 and `jitter` to 0. */
export interface RetryPolicyOptions {
  /** The wait before the first retry, before jitter. */
  baseMs: number;
  /** However far the curve grows, and whatever a server asks, no wait is longer. */
  capMs: number;
  /** What each retry multiplies the wait by. */
  growth?: number;
  /** The share of each wait drawn at random: 0 none, 0.5 half, 1 all of it. */
  jitter?: number;
}

/** What one wait takes besides its attempt. */
export interface WaitOptions {
  /** How long the server asked to wait (its `Retry-After`), when it did. */
  retryAfterMs?: number;
  /** The source of the jitter, a number in [0, 1); `Math.random` unless handed in. */
  draw?: () => number;
}

/**
 * One growing, capped wait between attempts, as a value: the app's retry and
 * the portal's reconnect both wait by one. The base grows by `growth` per
 * attempt up to the cap, and the `jitter` share of it is drawn, so callers
 * that failed together do not return together. With half of it drawn, the
 * shortest wait of one attempt is the longest wait of the one before it
 * wherever the curve doubles, which is what makes the growth assertable.
 */
export class RetryPolicy {
  readonly baseMs: number;
  readonly capMs: number;
  readonly growth: number;
  readonly jitter: number;

  constructor({ baseMs, capMs, growth = 2, jitter = 0 }: RetryPolicyOptions) {
    this.baseMs = baseMs;
    this.capMs = capMs;
    this.growth = growth;
    this.jitter = jitter;
  }

  /**
   * The wait before retry `attempt` (1 is the first). A server's longer ask
   * is its floor and the cap is its bound, so an ask for minutes does not
   * hold a screen that long. The power past the cap runs to Infinity, which
   * the cap takes, so no attempt count gives a wait past it; a zero base
   * stays zero, where Infinity times it would be no number at all.
   */
  waitMs(attempt: number, { retryAfterMs, draw = Math.random }: WaitOptions = {}): number {
    const grown = this.baseMs === 0 ? 0 : this.baseMs * this.growth ** Math.max(0, attempt - 1);
    const curve = Math.min(grown, this.capMs);
    const drawn = curve * this.jitter;
    const wait = curve - drawn + drawn * draw();
    return Math.min(Math.max(wait, retryAfterMs ?? 0), this.capMs);
  }
}

/**
 * One loop's count of failures over a policy: each failure counts one more
 * and gives the wait before the next try, and a success starts it over.
 */
export class Retries {
  readonly policy: RetryPolicy;
  readonly #draw: (() => number) | undefined;
  #failures = 0;

  constructor(policy: RetryPolicy, draw?: () => number) {
    this.policy = policy;
    this.#draw = draw;
  }

  /** The failures since the last reset; 0 before the first. */
  get failures(): number {
    return this.#failures;
  }

  /** Counts one more failure and gives the wait before the next try. */
  failed(retryAfterMs?: number): number {
    this.#failures += 1;
    return this.policy.waitMs(this.#failures, { retryAfterMs, draw: this.#draw });
  }

  /** Starts the count over, after a success. */
  reset(): void {
    this.#failures = 0;
  }
}

/**
 * The policy the transport client retries by: the base doubled per attempt
 * up to `MAX_RETRY_DELAY_MS`, half of each wait fixed and half drawn.
 */
export function requestRetryPolicy(baseMs: number = DEFAULT_RETRY_BASE_DELAY_MS): RetryPolicy {
  return new RetryPolicy({ baseMs, capMs: MAX_RETRY_DELAY_MS, jitter: 0.5 });
}

/**
 * A `Retry-After` in seconds, as the gateway sends it, in milliseconds; an
 * HTTP date or anything unreadable is no ask at all.
 */
export function retryAfterHeaderMs(header: string | null): number | undefined {
  if (header === null || !/^\d+$/.test(header.trim())) return undefined;
  return Number(header.trim()) * 1000;
}
