import { describe, expect, it } from "vitest";
import { DEFAULT_RETRY_ATTEMPTS, DEFAULT_RETRY_BASE_DELAY_MS } from "../api";
import { DEFAULT_REQUEST_TIMEOUT_MS, resolveConfig } from "./config";

const defaults = {
  devSignIn: false,
  requestTimeoutMs: DEFAULT_REQUEST_TIMEOUT_MS,
  retryAttempts: DEFAULT_RETRY_ATTEMPTS,
  retryBaseDelayMs: DEFAULT_RETRY_BASE_DELAY_MS,
};

const origin = "https://d111.cloudfront.net";

describe("runtime config", () => {
  it("uses the fetched config, with an empty apiUrl meaning the page's origin", () => {
    expect(resolveConfig({ apiUrl: "", sentryDsn: "https://k@s/1", environment: "dev" }, {}, origin)).toEqual({
      apiUrl: origin,
      sentryDsn: "https://k@s/1",
      environment: "dev",
      ...defaults,
    });
    expect(resolveConfig({ apiUrl: "https://api.example.test" }, {}, origin)).toEqual({
      apiUrl: "https://api.example.test",
      sentryDsn: "",
      environment: "unknown",
      ...defaults,
    });
  });

  it("takes the request timeout from the config when it is a positive number", () => {
    expect(resolveConfig({ apiUrl: "", requestTimeoutMs: 5_000 }, {}, origin).requestTimeoutMs).toBe(5_000);
    for (const notATimeout of [0, -1, "5000", null, Number.NaN, Number.POSITIVE_INFINITY]) {
      expect(resolveConfig({ apiUrl: "", requestTimeoutMs: notATimeout }, {}, origin).requestTimeoutMs).toBe(
        DEFAULT_REQUEST_TIMEOUT_MS,
      );
    }
  });

  it("takes the retry count and the first delay from the config, zero attempts included", () => {
    const config = resolveConfig({ apiUrl: "", retryAttempts: 0, retryBaseDelayMs: 100 }, {}, origin);
    expect(config).toMatchObject({ retryAttempts: 0, retryBaseDelayMs: 100 });
    for (const notACount of [-1, 1.5, "2", null, Number.NaN]) {
      expect(resolveConfig({ apiUrl: "", retryAttempts: notACount }, {}, origin).retryAttempts).toBe(
        DEFAULT_RETRY_ATTEMPTS,
      );
    }
    for (const notADelay of [0, -1, "250", null, Number.POSITIVE_INFINITY]) {
      expect(
        resolveConfig({ apiUrl: "", retryBaseDelayMs: notADelay }, {}, origin).retryBaseDelayMs,
      ).toBe(DEFAULT_RETRY_BASE_DELAY_MS);
    }
  });

  it("falls back to the build variables when there is no config file", () => {
    const env = { VITE_API_URL: "http://127.0.0.1:8000", VITE_SENTRY_DSN: "http://k@localhost:58000/1" };
    for (const notAConfig of [null, "<!doctype html>", [], { apiUrl: 42 }]) {
      expect(resolveConfig(notAConfig, env, origin)).toEqual({
        apiUrl: "http://127.0.0.1:8000",
        sentryDsn: "http://k@localhost:58000/1",
        environment: "local",
        ...defaults,
        devSignIn: true,
      });
    }
  });

  it("calls the API on the page's own origin when no build variable names another", () => {
    // The dev server and the portal container forward /v1 to the API, as
    // the cloud's distribution does, so local calls are same-origin too.
    expect(resolveConfig(null, {}, origin).apiUrl).toBe(origin);
    expect(resolveConfig(null, { VITE_API_URL: "" }, origin).apiUrl).toBe(origin);
  });

  it("offers the local sign-in locally and only where a config names it", () => {
    expect(resolveConfig({ apiUrl: "", devSignIn: true }, {}, origin).devSignIn).toBe(true);
    expect(resolveConfig({ apiUrl: "", devSignIn: "yes" }, {}, origin).devSignIn).toBe(false);
    expect(resolveConfig(null, {}, origin).devSignIn).toBe(true);
    expect(resolveConfig(null, { VITE_DEV_SIGN_IN: "false" }, origin).devSignIn).toBe(false);
  });
});
