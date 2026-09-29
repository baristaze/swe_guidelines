import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, createClient, RequestTimeout, type ClientOptions } from "./client";

// A fetch that never answers on its own and rejects with the signal's reason
// once it is aborted, or at once when it already is, the way a real fetch does.
function hangingFetch(): typeof fetch {
  return (_input, init) =>
    new Promise((_, reject) => {
      const signal = init?.signal;
      if (signal?.aborted) reject(signal.reason);
      signal?.addEventListener("abort", () => reject(signal.reason));
    });
}

function jsonResponse(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...headers },
  });
}

function textResponse(status: number, body: string, contentType = "text/html"): Response {
  return new Response(body, {
    status,
    statusText: status === 502 ? "Bad Gateway" : "",
    headers: { "content-type": contentType, "x-request-id": "req_9" },
  });
}

// One attempt, so a case about what a single call does is not also told
// about the retry; the cases below that are about the retry ask for it.
function client(overrides: Partial<ClientOptions> = {}) {
  return createClient({
    baseUrl: "https://api.example.test/",
    app: "portal",
    appVersion: "portal@test",
    timeoutMs: 20,
    retryAttempts: 0,
    retryBaseDelayMs: 0,
    getToken: () => "tok_1",
    onUnauthorized: () => undefined,
    ...overrides,
  });
}

afterEach(() => {
  vi.useRealTimers();
});

describe("transport client deadline", () => {
  it("rejects a call whose response never arrives once the timeout passes", async () => {
    const api = client({ fetchImpl: hangingFetch() });
    const started = Date.now();
    const failure = await api.get("/v1/tasks").catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(RequestTimeout);
    expect(failure).toMatchObject({ method: "GET", path: "/v1/tasks", timeoutMs: 20 });
    expect(Date.now() - started).toBeGreaterThanOrEqual(15);
  });

  it("counts reading the body against the same deadline", async () => {
    const fetchImpl: typeof fetch = (_input, init) =>
      Promise.resolve({
        ok: true,
        status: 200,
        headers: new Headers(),
        text: () =>
          new Promise<string>((_, reject) => {
            init?.signal?.addEventListener("abort", () => reject(init.signal?.reason));
          }),
      } as unknown as Response);
    const failure = await client({ fetchImpl }).get("/v1/tasks").catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(RequestTimeout);
  });

  it("hands every call a signal, and drops the timer once the call is done", async () => {
    vi.useFakeTimers();
    const fetchImpl = vi.fn<typeof fetch>(() => Promise.resolve(jsonResponse(200, { id: "t1" })));
    const api = client({ fetchImpl, timeoutMs: 30_000 });
    await expect(api.get("/v1/tasks/t1")).resolves.toEqual({ id: "t1" });
    const init = fetchImpl.mock.calls[0]?.[1];
    expect(init?.signal).toBeInstanceOf(AbortSignal);
    expect(init?.signal?.aborted).toBe(false);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("keeps the caller's own abort, which is not a timeout", async () => {
    const api = client({ fetchImpl: hangingFetch(), timeoutMs: 30_000 });
    const controller = new AbortController();
    const pending = api.get("/v1/tasks", { signal: controller.signal }).catch((error: unknown) => error);
    controller.abort(new Error("navigated away"));
    const failure = await pending;
    expect(failure).toBeInstanceOf(Error);
    expect(failure).not.toBeInstanceOf(RequestTimeout);
    expect((failure as Error).message).toBe("navigated away");
  });

  it("refuses at once a call whose signal is already aborted", async () => {
    const fetchImpl = vi.fn<typeof fetch>(hangingFetch());
    const controller = new AbortController();
    controller.abort(new Error("already gone"));
    const failure = await client({ fetchImpl })
      .get("/v1/tasks", { signal: controller.signal })
      .catch((error: unknown) => error);
    expect((failure as Error).message).toBe("already gone");
  });

  it("still parses the error envelope into a typed error", async () => {
    const fetchImpl: typeof fetch = () =>
      Promise.resolve(
        jsonResponse(404, { error: { code: "not_found", message: "no such task", request_id: "req_1" } }),
      );
    const failure = await client({ fetchImpl }).get("/v1/tasks/x").catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(ApiError);
    expect(failure).toMatchObject({ status: 404, code: "not_found", requestId: "req_1" });
    expect((failure as ApiError).planLimit).toBeNull();
  });

  it("carries the bound a plan's refusal met, and nothing else changes", async () => {
    const planLimit = { lever: "active_tasks", plan: "free", limit: 10, suggested_plan: "pro" };
    const fetchImpl: typeof fetch = () =>
      Promise.resolve(
        jsonResponse(402, {
          error: {
            code: "plan_limit_reached",
            message: "the free plan allows 10 active tasks",
            request_id: "req_2",
            plan_limit: planLimit,
          },
        }),
      );
    const failure = await client({ fetchImpl }).post("/v1/tasks", { title: "x" }).catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(ApiError);
    expect(failure).toMatchObject({ status: 402, code: "plan_limit_reached", requestId: "req_2", planLimit });
  });

  it("carries where a trimmed stream goes on from", async () => {
    const stream = { floor: 7, head: 9 };
    const fetchImpl: typeof fetch = () =>
      Promise.resolve(
        jsonResponse(410, {
          error: { code: "stream_truncated", message: "gone", request_id: "req_3", stream },
        }),
      );
    const failure = await client({ fetchImpl }).get("/v1/events?after_seq=5").catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(ApiError);
    expect(failure).toMatchObject({ status: 410, code: "stream_truncated", stream, planLimit: null });
  });
});

describe("transport client response handling", () => {
  it("turns a proxy's HTML 502 into a typed error carrying the status", async () => {
    const fetchImpl: typeof fetch = () => Promise.resolve(textResponse(502, "<html>Bad Gateway</html>"));
    const failure = await client({ fetchImpl }).get("/v1/tasks").catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(ApiError);
    expect(failure).toMatchObject({ status: 502, code: "unknown_error", requestId: "req_9" });
    expect((failure as ApiError).message).toBe("Bad Gateway");
  });

  it("names the status when a 504 comes with no status text", async () => {
    const fetchImpl: typeof fetch = () =>
      Promise.resolve(new Response("timeout", { status: 504, headers: { "content-type": "text/plain" } }));
    const failure = await client({ fetchImpl }).get("/v1/tasks").catch((error: unknown) => error);
    expect(failure).toMatchObject({ status: 504, code: "unknown_error", message: "HTTP 504" });
  });

  it("clears authentication on a 401 whose body is not JSON", async () => {
    const onUnauthorized = vi.fn();
    const fetchImpl: typeof fetch = () => Promise.resolve(textResponse(401, "Unauthorized", "text/plain"));
    const failure = await client({ fetchImpl, onUnauthorized }).get("/v1/me").catch((error: unknown) => error);
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
    // The refused token is named, so a switch in flight can tell it is the one it hands over.
    expect(onUnauthorized).toHaveBeenCalledWith("tok_1");
    expect(failure).toBeInstanceOf(ApiError);
    expect(failure).toMatchObject({ status: 401 });
  });

  it("clears authentication on a 401 with the envelope too", async () => {
    const onUnauthorized = vi.fn();
    const fetchImpl: typeof fetch = () =>
      Promise.resolve(jsonResponse(401, { error: { code: "not_authenticated", message: "no", request_id: "r" } }));
    await client({ fetchImpl, onUnauthorized }).get("/v1/me").catch(() => undefined);
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });

  it("keeps the session on a 401 from a request that presented no bearer", async () => {
    // Signing in presents none, and /sign-in is reachable while signed in, so
    // a mistyped password there must not sign the person out of the tab.
    const onUnauthorized = vi.fn();
    const fetchImpl: typeof fetch = () =>
      Promise.resolve(jsonResponse(401, { error: { code: "invalid_credential", message: "no", request_id: "r" } }));
    const failure = await client({ fetchImpl, onUnauthorized })
      .request("POST", "/v1/auth/login", { email: "a@b.test", password: "wrong" }, { token: null })
      .catch((error: unknown) => error);
    expect(onUnauthorized).not.toHaveBeenCalled();
    expect(failure).toMatchObject({ status: 401, code: "invalid_credential" });
  });

  it("refuses a success whose body is not JSON with a typed error, never a SyntaxError", async () => {
    const fetchImpl: typeof fetch = () => Promise.resolve(textResponse(200, "<html>captive portal</html>"));
    const failure = await client({ fetchImpl }).get("/v1/tasks").catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(ApiError);
    expect(failure).toMatchObject({ status: 200, code: "not_json", requestId: "req_9" });
  });

  it("treats a JSON error body that does not parse as an error with the status", async () => {
    const fetchImpl: typeof fetch = () =>
      Promise.resolve(new Response("{not json", { status: 500, headers: { "content-type": "application/json" } }));
    const failure = await client({ fetchImpl }).get("/v1/tasks").catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(ApiError);
    expect(failure).toMatchObject({ status: 500, code: "unknown_error" });
  });

  it("returns nothing for an empty success", async () => {
    const fetchImpl: typeof fetch = () => Promise.resolve(new Response(null, { status: 204 }));
    await expect(client({ fetchImpl }).del("/v1/tasks/t1")).resolves.toBeUndefined();
  });
});

it("keeps a replacement session when an older request returns 401", async () => {
  let token = "ses_old";
  let respond!: (response: Response) => void;
  const onUnauthorized = vi.fn();
  const api = client({
    getToken: () => token, onUnauthorized,
    fetchImpl: () => new Promise((resolve) => { respond = resolve; }),
  });
  const pending = api.get("/v1/me").catch((error: unknown) => error);
  token = "ses_new";
  respond(textResponse(401, "Unauthorized"));
  expect(await pending).toMatchObject({ status: 401 });
  expect(onUnauthorized).not.toHaveBeenCalled();
});

it("keeps the session when a separate login credential is refused", async () => {
  const onUnauthorized = vi.fn();
  const api = client({
    onUnauthorized,
    fetchImpl: async () => textResponse(401, "Unauthorized"),
  });
  await expect(api.post("/v1/auth/sessions", { org_id: "o1" }, { token: "login_expired" }))
    .rejects.toMatchObject({ status: 401 });
  expect(onUnauthorized).not.toHaveBeenCalled();
});

describe("the transport client's one retry", () => {
  // The waits are zero here: the curve is pinned in retry.test.ts, and what
  // these cases are about is which call goes again and how many times.
  function retrying(fetchImpl: typeof fetch, overrides: Partial<ClientOptions> = {}) {
    return client({ fetchImpl, retryAttempts: 2, retryBaseDelayMs: 0, ...overrides });
  }

  it("retries an unavailable answer up to the bound, then surfaces it", async () => {
    const fetchImpl = vi.fn<typeof fetch>(() =>
      Promise.resolve(jsonResponse(503, { error: { code: "unavailable", message: "no", request_id: "r" } })),
    );
    const failure = await retrying(fetchImpl).get("/v1/tasks").catch((error: unknown) => error);
    expect(fetchImpl).toHaveBeenCalledTimes(3);
    expect(failure).toBeInstanceOf(ApiError);
    expect(failure).toMatchObject({ status: 503, code: "unavailable" });
  });

  it("carries the server's Retry-After on the error, in milliseconds", async () => {
    const fetchImpl = vi.fn<typeof fetch>(() =>
      Promise.resolve(
        new Response(JSON.stringify({ error: { code: "unavailable", message: "busy", request_id: "r" } }), {
          status: 503,
          headers: { "content-type": "application/json", "retry-after": "1" },
        }),
      ),
    );
    const failure = await client({ fetchImpl }).get("/v1/tasks").catch((error: unknown) => error);
    expect(failure).toMatchObject({ status: 503, retryAfterMs: 1000 });
  });

  it("stops as soon as an attempt answers", async () => {
    const answers = [jsonResponse(503, {}), jsonResponse(200, { id: "t1" })];
    const fetchImpl = vi.fn<typeof fetch>(() => Promise.resolve(answers.shift()!));
    await expect(retrying(fetchImpl).get("/v1/tasks/t1")).resolves.toEqual({ id: "t1" });
    expect(fetchImpl).toHaveBeenCalledTimes(2);
  });

  it("retries a deadline and a connection that failed before an answer", async () => {
    const timingOut = vi.fn<typeof fetch>(hangingFetch());
    const timedOut = await retrying(timingOut, { timeoutMs: 5 })
      .get("/v1/tasks")
      .catch((error: unknown) => error);
    expect(timedOut).toBeInstanceOf(RequestTimeout);
    expect(timingOut).toHaveBeenCalledTimes(3);

    const refused = vi.fn<typeof fetch>(() => Promise.reject(new TypeError("Failed to fetch")));
    await expect(retrying(refused).get("/v1/tasks")).rejects.toThrow("Failed to fetch");
    expect(refused).toHaveBeenCalledTimes(3);
  });

  it("sends a refusal once: a decision does not change because it is asked again", async () => {
    for (const status of [400, 403, 404, 409, 412, 422, 429, 500]) {
      const fetchImpl = vi.fn<typeof fetch>(() =>
        Promise.resolve(jsonResponse(status, { error: { code: "no", message: "no", request_id: "r" } })),
      );
      await expect(retrying(fetchImpl).get("/v1/tasks")).rejects.toBeInstanceOf(ApiError);
      expect(fetchImpl).toHaveBeenCalledTimes(1);
    }
  });

  it("sends a creating POST again under the key the first attempt carried", async () => {
    const answers = [jsonResponse(503, {}), jsonResponse(201, { id: "t1" })];
    const fetchImpl = vi.fn<typeof fetch>(() => Promise.resolve(answers.shift()!));
    await expect(
      retrying(fetchImpl).post("/v1/tasks", { title: "one" }, { idempotencyKey: "key_1" }),
    ).resolves.toEqual({ id: "t1" });
    expect(fetchImpl).toHaveBeenCalledTimes(2);
    for (const [, init] of fetchImpl.mock.calls) {
      expect(new Headers(init?.headers).get("Idempotency-Key")).toBe("key_1");
    }
  });

  it("names the version a write read as the entity tag If-Match carries", async () => {
    const fetchImpl = vi.fn<typeof fetch>(() => Promise.resolve(jsonResponse(200, { id: "t1" })));
    const api = retrying(fetchImpl);
    await api.patch("/v1/tasks/t1", { title: "t" }, { ifMatch: 3 });
    await api.del("/v1/tasks/t1", { ifMatch: 4 });
    await api.get("/v1/tasks/t1");
    const sent = fetchImpl.mock.calls.map(([, init]) => new Headers(init?.headers).get("If-Match"));
    expect(sent).toEqual(['"3"', '"4"', null]);
  });

  it("sends a write with no key exactly once, whatever the failure", async () => {
    // Nothing records the outcome of these, so a second attempt could write
    // twice: the failure is told to the caller instead.
    const fetchImpl = vi.fn<typeof fetch>(() => Promise.resolve(jsonResponse(503, {})));
    const api = retrying(fetchImpl);
    await expect(api.post("/v1/auth/logout")).rejects.toBeInstanceOf(ApiError);
    await expect(api.patch("/v1/tasks/t1", { title: "t" }, { ifMatch: 1 })).rejects.toBeInstanceOf(ApiError);
    await expect(api.del("/v1/tasks/t1")).rejects.toBeInstanceOf(ApiError);
    expect(fetchImpl).toHaveBeenCalledTimes(3);
  });

  it("does not attempt again once the caller has given up", async () => {
    const controller = new AbortController();
    const fetchImpl = vi.fn<typeof fetch>((_input, init) => {
      controller.abort(new Error("navigated away"));
      return Promise.reject(init?.signal?.reason ?? new TypeError("Failed to fetch"));
    });
    const failure = await retrying(fetchImpl)
      .get("/v1/tasks", { signal: controller.signal })
      .catch((error: unknown) => error);
    expect((failure as Error).message).toBe("navigated away");
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it("sends every call exactly once when the settings turn the retry off", async () => {
    const fetchImpl = vi.fn<typeof fetch>(() => Promise.resolve(jsonResponse(503, {})));
    await expect(client({ fetchImpl }).get("/v1/tasks")).rejects.toBeInstanceOf(ApiError);
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });
});

describe("transport client bytes", () => {
  it("sends a file's bytes with their own type and reads the JSON answer", async () => {
    const seen: RequestInit[] = [];
    const bytes = new Blob(["%PDF!"]);
    const answer = await client({
      fetchImpl: async (_input, init) => {
        seen.push(init!);
        return jsonResponse(200, { id: "f1" });
      },
    }).putBytes<{ id: string }>("/v1/media/files/f1/content", bytes, "application/pdf");
    expect(answer).toEqual({ id: "f1" });
    expect(seen[0]!.method).toBe("PUT");
    expect(seen[0]!.body).toBe(bytes);
    expect(new Headers(seen[0]!.headers).get("content-type")).toBe("application/pdf");
  });

  it("reads a file's bytes as a blob, and a refusal still as the envelope", async () => {
    const got = await client({
      fetchImpl: async () => new Response("%PDF!", { status: 200, headers: { "content-type": "application/pdf" } }),
    }).getBlob("/v1/media/files/f1/content");
    expect(await got.text()).toBe("%PDF!");
    const refused = client({
      fetchImpl: async () => jsonResponse(404, { error: { code: "not_found", message: "file f1 not found", request_id: "r1" } }),
    }).getBlob("/v1/media/files/f1/content");
    await expect(refused).rejects.toMatchObject({ status: 404, code: "not_found" });
  });
});
