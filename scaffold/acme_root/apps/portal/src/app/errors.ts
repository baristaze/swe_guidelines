// Error reporting to a Sentry-compatible backend (GlitchTip locally). It is on
// only when the runtime config names a DSN; without one every call is a no-op.
import * as Sentry from "@sentry/react";
import type { RuntimeConfig } from "./config";

export function initErrorReporting(config: RuntimeConfig): void {
  if (!config.sentryDsn) return;
  Sentry.init({
    dsn: config.sentryDsn,
    environment: config.environment,
    release: __APP_VERSION__,
    sendDefaultPii: false,
  });
}

export function reportError(error: unknown): void {
  Sentry.captureException(error);
}
