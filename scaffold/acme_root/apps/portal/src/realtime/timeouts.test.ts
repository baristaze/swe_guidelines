// The portal is a consumer of the pin: the file is read from disk here, apart
// from the module's own import, so a value the module carries that is not the
// one deployment pinned fails this test.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { backoffDelay, LOAD_BALANCER_IDLE_TIMEOUT_MS, PING_INTERVAL_MS } from "./timeouts";

const PINNED_FILE = new URL("../../../../deployment/realtime-timeouts.json", import.meta.url);

interface Pinned {
  ping_interval_seconds: number;
  server_ping_interval_seconds: number;
  server_ping_timeout_seconds: number;
  load_balancer_idle_timeout_seconds: number;
}

function pinned(): Pinned {
  const parsed: unknown = JSON.parse(readFileSync(PINNED_FILE, "utf8"));
  expect(parsed).toMatchObject({
    ping_interval_seconds: expect.any(Number),
    server_ping_interval_seconds: expect.any(Number),
    server_ping_timeout_seconds: expect.any(Number),
    load_balancer_idle_timeout_seconds: expect.any(Number),
  });
  return parsed as Pinned;
}

describe("realtime timeouts", () => {
  it("carries the ping interval and the idle timeout the deployment pinned", () => {
    const pin = pinned();
    expect(PING_INTERVAL_MS).toBe(pin.ping_interval_seconds * 1000);
    expect(LOAD_BALANCER_IDLE_TIMEOUT_MS).toBe(pin.load_balancer_idle_timeout_seconds * 1000);
  });

  it("pings well inside the load balancer idle timeout", () => {
    const pin = pinned();
    expect(pin.ping_interval_seconds * 2).toBeLessThanOrEqual(pin.load_balancer_idle_timeout_seconds);
    expect(PING_INTERVAL_MS * 2).toBeLessThanOrEqual(LOAD_BALANCER_IDLE_TIMEOUT_MS);
  });

  it("leaves the server's ping and its timeout inside the idle timeout too", () => {
    // The server ends a dead socket before the load balancer would: the
    // protocol ping it sends, plus the wait for the pong, fits the idle timeout.
    const pin = pinned();
    expect(pin.server_ping_interval_seconds + pin.server_ping_timeout_seconds).toBeLessThan(
      pin.load_balancer_idle_timeout_seconds,
    );
  });

  it("backs off exponentially with a cap, which is the top of each window", () => {
    // The curve and the cap are unchanged; the jitter is handed in, so these
    // are the same 1s, 4s, and 30s the file pinned before it carried any.
    expect(backoffDelay(1, () => 1)).toBe(1_000);
    expect(backoffDelay(3, () => 1)).toBe(4_000);
    expect(backoffDelay(10, () => 1)).toBe(30_000);
  });

  it("carries jitter, so tabs dropped together do not come back together", () => {
    // Half of each wait is jitter and half is fixed, so the shortest wait of
    // an attempt is the longest wait of the one before it and the delay grows
    // whatever the randomness answers.
    expect(backoffDelay(1, () => 0)).toBe(500);
    expect(backoffDelay(2, () => 0)).toBe(1_000);
    expect(backoffDelay(3, () => 0)).toBe(2_000);
    expect(backoffDelay(10, () => 0)).toBe(15_000);
    const spread = new Set([0, 0.25, 0.5, 0.75].map((r) => backoffDelay(2, () => r)));
    expect(spread.size).toBe(4);
    for (const delay of spread) {
      expect(delay).toBeGreaterThanOrEqual(1_000);
      expect(delay).toBeLessThanOrEqual(2_000);
    }
  });

  it("stays inside the window with the real source of randomness too", () => {
    for (let i = 0; i < 20; i += 1) {
      const delay = backoffDelay(1);
      expect(delay).toBeGreaterThanOrEqual(500);
      expect(delay).toBeLessThanOrEqual(1_000);
    }
  });
});
