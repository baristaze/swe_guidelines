// @vitest-environment jsdom
// The sign-in pages carry a capability in their query: the invitation token on
// /login and the one-time code on /auth/callback. No report the portal sends
// keeps it, in a breadcrumb or in the event's own request.
import * as Sentry from "@sentry/react";
import type { ErrorEvent } from "@sentry/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { initErrorReporting, outgoingBreadcrumb, outgoingEvent, reportError, withoutQuery } from "./errors";

const INVITE = "/login?invitation_token=tok-123";
const CALLBACK = "/auth/callback?code=code-456&state=state-789#frag-000";
const SECRETS = /tok-123|code-456|state-789|frag-000/;

describe("withoutQuery", () => {
  it("cuts a URL at its query or its fragment, whichever comes first", () => {
    expect(withoutQuery(INVITE)).toBe("/login");
    expect(withoutQuery(CALLBACK)).toBe("/auth/callback");
    expect(withoutQuery("https://acme.test/app#a?b")).toBe("https://acme.test/app");
    expect(withoutQuery("https://acme.test/v1/me")).toBe("https://acme.test/v1/me");
  });
});

describe("outgoingBreadcrumb", () => {
  it("keeps a navigation's paths and drops their queries", () => {
    const crumb = outgoingBreadcrumb({ category: "navigation", data: { from: INVITE, to: CALLBACK } });
    expect(crumb.data).toEqual({ from: "/login", to: "/auth/callback" });
  });

  it("keeps a fetch's method, status, and path, and drops its query", () => {
    const crumb = outgoingBreadcrumb({
      category: "fetch",
      type: "http",
      data: { method: "GET", url: `https://acme.test/v1/sign-in${CALLBACK}`, status_code: 400 },
    });
    expect(crumb.data).toEqual({ method: "GET", url: "https://acme.test/v1/sign-in/auth/callback", status_code: 400 });
  });

  it("leaves a breadcrumb with no URL as it is", () => {
    expect(outgoingBreadcrumb({ category: "ui.click", message: "button.primary" })).toEqual({
      category: "ui.click",
      message: "button.primary",
    });
  });
});

describe("outgoingEvent", () => {
  it("drops the query from the page's URL and from its referrer", () => {
    const event = outgoingEvent({
      type: undefined,
      request: {
        url: `https://acme.test${CALLBACK}`,
        query_string: "code=code-456&state=state-789",
        headers: { Referer: `https://acme.test${INVITE}`, "User-Agent": "test" },
      },
    } as ErrorEvent);
    expect(event.request).toEqual({
      url: "https://acme.test/auth/callback",
      headers: { Referer: "https://acme.test/login", "User-Agent": "test" },
    });
  });
});

describe("initErrorReporting", () => {
  afterEach(async () => {
    await Sentry.close();
    vi.unstubAllGlobals();
  });

  it("sends an error from the sign-in pages with no query in its breadcrumbs or its request", async () => {
    const sent: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((_url: unknown, init?: RequestInit) => {
        if (typeof init?.body === "string") sent.push(init.body);
        return Promise.resolve(new Response("{}", { status: 200 }));
      }),
    );
    window.history.replaceState({}, "", INVITE);
    initErrorReporting({
      apiUrl: "https://acme.test",
      sentryDsn: "https://key@errors.acme.test/1",
      environment: "test",
      devSignIn: false,
      requestTimeoutMs: 1000,
      retryAttempts: 0,
      retryBaseDelayMs: 0,
    });
    // A request as the client makes it, through the page's fetch.
    await window.fetch("https://acme.test/v1/invitations?invitation_token=tok-123");
    window.history.pushState({}, "", CALLBACK);
    reportError(new Error("the callback failed"));
    await Sentry.flush(2000);

    const envelope = sent.find((body) => body.includes("the callback failed"));
    expect(envelope).toBeDefined();
    const event = JSON.parse(envelope!.split("\n")[2]!) as ErrorEvent;
    expect(event.request?.url).toBe("http://localhost:3000/auth/callback");
    expect(event.breadcrumbs).toContainEqual(
      expect.objectContaining({ category: "navigation", data: { from: "/login", to: "/auth/callback" } }),
    );
    expect(event.breadcrumbs).toContainEqual(
      expect.objectContaining({
        category: "fetch",
        data: expect.objectContaining({ url: "https://acme.test/v1/invitations" }) as unknown,
      }),
    );
    expect(envelope).not.toMatch(SECRETS);
  });
});
