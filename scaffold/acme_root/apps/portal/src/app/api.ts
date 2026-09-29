// The one client instance the whole app shares, built from the runtime config.
import { createClient } from "../api";
import { useSessionStore } from "../store/session";
import { runtimeConfig } from "./config";
import { forgetSessionIfHeld } from "./forgetSession";

export const api = createClient({
  baseUrl: runtimeConfig().apiUrl,
  app: "portal",
  appVersion: __APP_VERSION__,
  timeoutMs: runtimeConfig().requestTimeoutMs,
  retryAttempts: runtimeConfig().retryAttempts,
  retryBaseDelayMs: runtimeConfig().retryBaseDelayMs,
  getToken: () => useSessionStore.getState().token,
  // Through the same door as the socket's 4401, so a switch in flight holds it.
  onUnauthorized: forgetSessionIfHeld,
});
