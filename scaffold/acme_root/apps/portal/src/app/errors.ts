// Error reporting to a Sentry-compatible backend (GlitchTip locally). It is on
// only when the runtime config names a DSN; without one every call is a no-op.
import * as Sentry from "@sentry/react";
import type { Breadcrumb, ErrorEvent } from "@sentry/react";
import type { RuntimeConfig } from "./config";

export function initErrorReporting(config: RuntimeConfig): void {
  if (!config.sentryDsn) return;
  Sentry.init({
    dsn: config.sentryDsn,
    environment: config.environment,
    release: __APP_VERSION__,
    sendDefaultPii: false,
    beforeBreadcrumb: outgoingBreadcrumb,
    beforeSend: outgoingEvent,
  });
}

export function reportError(error: unknown): void {
  Sentry.captureException(error);
}

// A URL leaves in a report without its query and its fragment. The sign-in
// pages carry a capability there: the invitation token on /login, and the
// one-time code on /auth/callback.
export function withoutQuery(url: string): string {
  const end = url.search(/[?#]/);
  return end === -1 ? url : url.slice(0, end);
}

// Where a breadcrumb holds a URL: a fetch's or an XHR's `url`, and a
// navigation's `from` and `to`.
const URL_KEYS = ["url", "from", "to"] as const;

export function outgoingBreadcrumb(crumb: Breadcrumb): Breadcrumb {
  const data = crumb.data;
  if (data) {
    for (const key of URL_KEYS) {
      const value: unknown = data[key];
      if (typeof value === "string") data[key] = withoutQuery(value);
    }
  }
  return crumb;
}

// Every event carries the page's URL, and its referrer, as `request`.
export function outgoingEvent(event: ErrorEvent): ErrorEvent {
  const request = event.request;
  if (request) {
    if (request.url) request.url = withoutQuery(request.url);
    delete request.query_string;
    const headers = request.headers;
    if (headers?.Referer) headers.Referer = withoutQuery(headers.Referer);
  }
  return event;
}
