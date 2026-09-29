// The settings a build does not carry. In the cloud the portal's host serves
// /config.json, written per environment, so one build runs anywhere. Its
// apiUrl is empty: the portal's own origin serves the API's paths, so every
// call is same-origin and no browser sends a preflight.
// Locally there is none (the dev server and nginx answer with index.html),
// and the Vite build variables apply instead; with none of those either, the
// API is the page's origin there too, since the dev server and the portal
// container forward /v1 to it.
import { DEFAULT_RETRY_ATTEMPTS, DEFAULT_RETRY_BASE_DELAY_MS } from "../api";

export interface RuntimeConfig {
  /** Absolute base URL of the API; an empty value in the file means the page's origin. */
  apiUrl: string;
  sentryDsn: string;
  environment: string;
  /** The deadline of every call the transport client makes; one setting, one default. */
  requestTimeoutMs: number;
  /** Extra attempts a retryable failure gets; 0 sends every call exactly once. */
  retryAttempts: number;
  /** The wait before the first extra attempt; it doubles and carries jitter. */
  retryBaseDelayMs: number;
  /** Whether the local sign-in by address alone (`/login/dev`) is offered.
   * Only the local stack serves it; a deployed config never names it. */
  devSignIn: boolean;
}

export interface BuildEnv {
  VITE_API_URL?: string;
  VITE_SENTRY_DSN?: string;
  VITE_SENTRY_ENVIRONMENT?: string;
  VITE_DEV_SIGN_IN?: string;
}

export const DEFAULT_REQUEST_TIMEOUT_MS = 30_000;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function timeoutOrDefault(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : DEFAULT_REQUEST_TIMEOUT_MS;
}

/** A whole count of extra attempts. Zero is a value: it turns the retry off. */
function attemptsOrDefault(value: unknown): number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : DEFAULT_RETRY_ATTEMPTS;
}

function delayOrDefault(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : DEFAULT_RETRY_BASE_DELAY_MS;
}

/** Pure: the fetched config if it is one, else the build variables. An empty
 * apiUrl, in either, means the API shares the page's origin. */
export function resolveConfig(fetched: unknown, env: BuildEnv, origin: string): RuntimeConfig {
  if (isRecord(fetched) && typeof fetched.apiUrl === "string") {
    return {
      apiUrl: fetched.apiUrl || origin,
      sentryDsn: typeof fetched.sentryDsn === "string" ? fetched.sentryDsn : "",
      environment: typeof fetched.environment === "string" ? fetched.environment : "unknown",
      requestTimeoutMs: timeoutOrDefault(fetched.requestTimeoutMs),
      retryAttempts: attemptsOrDefault(fetched.retryAttempts),
      retryBaseDelayMs: delayOrDefault(fetched.retryBaseDelayMs),
      devSignIn: fetched.devSignIn === true,
    };
  }
  return {
    apiUrl: env.VITE_API_URL || origin,
    sentryDsn: env.VITE_SENTRY_DSN ?? "",
    environment: env.VITE_SENTRY_ENVIRONMENT ?? "local",
    requestTimeoutMs: DEFAULT_REQUEST_TIMEOUT_MS,
    retryAttempts: DEFAULT_RETRY_ATTEMPTS,
    retryBaseDelayMs: DEFAULT_RETRY_BASE_DELAY_MS,
    // No config file: the local stack, where the local sign-in is served.
    devSignIn: env.VITE_DEV_SIGN_IN !== "false",
  };
}

let current: RuntimeConfig | null = null;

export async function loadRuntimeConfig(): Promise<RuntimeConfig> {
  let fetched: unknown = null;
  try {
    // A static file of the page's own origin, not an API call.
    // eslint-disable-next-line no-restricted-globals
    const response = await fetch("/config.json", { cache: "no-store" });
    if (response.ok && response.headers.get("content-type")?.includes("json")) {
      fetched = await response.json();
    }
  } catch {
    // No config file: the build variables apply.
  }
  current = resolveConfig(fetched, import.meta.env, window.location.origin);
  return current;
}

export function runtimeConfig(): RuntimeConfig {
  if (current === null) throw new Error("runtime config read before loadRuntimeConfig()");
  return current;
}
