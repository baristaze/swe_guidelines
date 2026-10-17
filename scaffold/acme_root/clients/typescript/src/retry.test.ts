// The retry policy as pure rules: which request may go twice, which answer
// may differ, and the curve. The jitter is a function the test hands in, so
// the curve is asserted at both ends of its window instead of on randomness.
import { describe, expect, it } from "vitest";
import {
  MAX_RETRY_DELAY_MS,
  isRetryableStatus,
  mayRetryRequest,
  requestRetryPolicy,
  Retries,
  retryAfterHeaderMs,
  RetryPolicy,
} from "./retry";

describe("which request may be sent twice", () => {
  it("sends a read again, because a read leaves nothing behind", () => {
    expect(mayRetryRequest("GET")).toBe(true);
    expect(mayRetryRequest("get")).toBe(true);
    expect(mayRetryRequest("HEAD")).toBe(true);
  });

  it("sends a creating POST again only under its idempotency key", () => {
    expect(mayRetryRequest("POST", "1d3f-key")).toBe(true);
    expect(mayRetryRequest("POST")).toBe(false);
    expect(mayRetryRequest("POST", "")).toBe(false);
  });

  it("never sends a PATCH or a DELETE again, key or no key", () => {
    // Neither carries a key the gateway records the outcome under, so a lost
    // answer leaves the write in doubt and a second attempt could repeat it.
    expect(mayRetryRequest("PATCH")).toBe(false);
    expect(mayRetryRequest("PATCH", "1d3f-key")).toBe(false);
    expect(mayRetryRequest("DELETE")).toBe(false);
    expect(mayRetryRequest("DELETE", "1d3f-key")).toBe(false);
  });
});

describe("which answer may differ on a second attempt", () => {
  it("retries the unavailable answer and the two a proxy sends for it", () => {
    expect(isRetryableStatus(502)).toBe(true);
    expect(isRetryableStatus(503)).toBe(true);
    expect(isRetryableStatus(504)).toBe(true);
  });

  it("does not retry a decision the server made", () => {
    for (const status of [400, 401, 403, 404, 409, 422, 429, 500]) {
      expect(isRetryableStatus(status)).toBe(false);
    }
  });
});

describe("one wait between attempts", () => {
  const policy = new RetryPolicy({ baseMs: 100, capMs: 1_000, growth: 3, jitter: 0.5 });
  const at = (draw: number) => () => draw;

  it("grows by its growth from the base to the cap", () => {
    expect(policy.waitMs(1, { draw: at(1) })).toBe(100);
    expect(policy.waitMs(2, { draw: at(1) })).toBe(300);
    expect(policy.waitMs(3, { draw: at(1) })).toBe(900);
    expect(policy.waitMs(4, { draw: at(1) })).toBe(1_000);
    expect(new RetryPolicy({ baseMs: 100, capMs: 1_000 }).waitMs(3)).toBe(400);
  });

  it("stays at the cap however many attempts have failed", () => {
    // The power runs to Infinity long before this; the wait never does.
    expect(policy.waitMs(10_000, { draw: at(1) })).toBe(1_000);
    expect(policy.waitMs(10_000, { draw: at(0) })).toBe(500);
    expect(new RetryPolicy({ baseMs: 0, capMs: 1_000 }).waitMs(10_000)).toBe(0);
  });

  it("draws its jitter share of the wait, and a draw of 0 and of 1 give its bounds", () => {
    expect(policy.waitMs(2, { draw: at(0) })).toBe(150);
    expect(policy.waitMs(2, { draw: at(0.5) })).toBe(225);
    expect(policy.waitMs(2, { draw: at(1) })).toBe(300);
    const full = new RetryPolicy({ baseMs: 100, capMs: 1_000, jitter: 1 });
    expect(full.waitMs(2, { draw: at(0) })).toBe(0);
    expect(full.waitMs(2, { draw: at(1) })).toBe(200);
    const none = new RetryPolicy({ baseMs: 100, capMs: 1_000 });
    expect(none.waitMs(2, { draw: at(0) })).toBe(200);
    expect(none.waitMs(2, { draw: at(1) })).toBe(200);
  });

  it("waits at least what the server asked, and never past the cap", () => {
    expect(policy.waitMs(1, { retryAfterMs: 700, draw: at(1) })).toBe(700);
    expect(policy.waitMs(3, { retryAfterMs: 700, draw: at(1) })).toBe(900);
    expect(policy.waitMs(1, { retryAfterMs: 60_000, draw: at(1) })).toBe(1_000);
  });
});

describe("one loop's count of failures", () => {
  it("counts each failure, waits by it, and starts over on a reset", () => {
    const retries = new Retries(new RetryPolicy({ baseMs: 100, capMs: 1_000 }), () => 0);
    expect(retries.failures).toBe(0);
    expect(retries.failed()).toBe(100);
    expect(retries.failed()).toBe(200);
    expect(retries.failed(700)).toBe(700);
    expect(retries.failures).toBe(3);
    retries.reset();
    expect(retries.failures).toBe(0);
    expect(retries.failed()).toBe(100);
  });

  it("takes its jitter from the draw it was handed", () => {
    const policy = new RetryPolicy({ baseMs: 100, capMs: 1_000, jitter: 0.5 });
    expect(new Retries(policy, () => 0).failed()).toBe(50);
    expect(new Retries(policy, () => 1).failed()).toBe(100);
  });
});

describe("the client's delay between attempts", () => {
  const client = requestRetryPolicy();
  const wait = (attempt: number, draw: number) => client.waitMs(attempt, { draw: () => draw });

  it("grows, and every attempt's shortest wait is the last one's longest", () => {
    expect(wait(1, 0)).toBe(125);
    expect(wait(1, 1)).toBe(250);
    expect(wait(2, 0)).toBe(250);
    expect(wait(2, 1)).toBe(500);
    expect(wait(3, 0)).toBe(500);
    expect(wait(3, 1)).toBe(1_000);
  });

  it("carries jitter inside that window, so callers do not return together", () => {
    const spread = new Set([0, 0.25, 0.5, 0.75].map((r) => wait(2, r)));
    expect(spread.size).toBe(4);
    for (const delay of spread) {
      expect(delay).toBeGreaterThanOrEqual(250);
      expect(delay).toBeLessThanOrEqual(500);
    }
  });

  it("stays inside the window with the real source of randomness too", () => {
    for (let i = 0; i < 50; i += 1) {
      const delay = client.waitMs(1);
      expect(delay).toBeGreaterThanOrEqual(125);
      expect(delay).toBeLessThanOrEqual(250);
    }
  });

  it("caps however far the doubling runs", () => {
    expect(wait(20, 1)).toBe(MAX_RETRY_DELAY_MS);
    expect(wait(20, 0)).toBe(MAX_RETRY_DELAY_MS / 2);
  });

  it("lengthens a shorter wait for the server's ask, never shortens one, and stops at the cap", () => {
    const asked = (attempt: number, retryAfterMs?: number) => client.waitMs(attempt, { retryAfterMs, draw: () => 1 });
    expect(asked(1, 1000)).toBe(1000);
    expect(asked(4, 1000)).toBe(2000);
    expect(asked(1)).toBe(250);
    expect(asked(1, 60_000)).toBe(5_000);
  });
});

describe("the server's Retry-After", () => {
  it("is read in whole seconds and ignored in any other shape", () => {
    expect(retryAfterHeaderMs("2")).toBe(2000);
    expect(retryAfterHeaderMs(" 1 ")).toBe(1000);
    expect(retryAfterHeaderMs(null)).toBeUndefined();
    expect(retryAfterHeaderMs("Wed, 21 Oct 2026 07:28:00 GMT")).toBeUndefined();
  });
});
