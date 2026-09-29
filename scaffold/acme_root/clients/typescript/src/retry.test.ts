// The retry policy as pure rules: which request may go twice, which answer
// may differ, and the curve. The jitter is a function the test hands in, so
// the curve is asserted at both ends of its window instead of on randomness.
import { describe, expect, it } from "vitest";
import {
  DEFAULT_RETRY_BASE_DELAY_MS,
  MAX_RETRY_DELAY_MS,
  isRetryableStatus,
  mayRetryRequest,
  retryAfterHeaderMs,
  retryDelayMs,
  retryWaitMs,
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

describe("the delay between attempts", () => {
  it("grows, and every attempt's shortest wait is the last one's longest", () => {
    const base = DEFAULT_RETRY_BASE_DELAY_MS;
    expect(retryDelayMs(1, base, () => 0)).toBe(125);
    expect(retryDelayMs(1, base, () => 1)).toBe(250);
    expect(retryDelayMs(2, base, () => 0)).toBe(250);
    expect(retryDelayMs(2, base, () => 1)).toBe(500);
    expect(retryDelayMs(3, base, () => 0)).toBe(500);
    expect(retryDelayMs(3, base, () => 1)).toBe(1_000);
  });

  it("carries jitter inside that window, so callers do not return together", () => {
    const spread = new Set([0, 0.25, 0.5, 0.75].map((r) => retryDelayMs(2, 250, () => r)));
    expect(spread.size).toBe(4);
    for (const delay of spread) {
      expect(delay).toBeGreaterThanOrEqual(250);
      expect(delay).toBeLessThanOrEqual(500);
    }
  });

  it("stays inside the window with the real source of randomness too", () => {
    for (let i = 0; i < 50; i += 1) {
      const delay = retryDelayMs(1, 250);
      expect(delay).toBeGreaterThanOrEqual(125);
      expect(delay).toBeLessThanOrEqual(250);
    }
  });

  it("caps however far the doubling runs", () => {
    expect(retryDelayMs(20, 250, () => 1)).toBe(MAX_RETRY_DELAY_MS);
    expect(retryDelayMs(20, 250, () => 0)).toBe(MAX_RETRY_DELAY_MS / 2);
  });
});

describe("the server's Retry-After", () => {
  it("is read in whole seconds and ignored in any other shape", () => {
    expect(retryAfterHeaderMs("2")).toBe(2000);
    expect(retryAfterHeaderMs(" 1 ")).toBe(1000);
    expect(retryAfterHeaderMs(null)).toBeUndefined();
    expect(retryAfterHeaderMs("Wed, 21 Oct 2026 07:28:00 GMT")).toBeUndefined();
  });

  it("lengthens a shorter wait, never shortens one, and stops at the cap", () => {
    expect(retryWaitMs(200, 1000)).toBe(1000);
    expect(retryWaitMs(1500, 1000)).toBe(1500);
    expect(retryWaitMs(200)).toBe(200);
    expect(retryWaitMs(200, 60_000)).toBe(5_000);
  });
});
