// One transport client: bearer, app header, the error envelope parsed into
// a typed error carrying the request id, a 401 that clears authentication,
// a deadline on every call, and the app's one retry. The one file in the app
// that may call fetch.

import type { LastOwnerDetail, OwnedOrgRef } from "./types";
import {
  DEFAULT_RETRY_ATTEMPTS,
  DEFAULT_RETRY_BASE_DELAY_MS,
  isRetryableStatus,
  mayRetryRequest,
  retryAfterHeaderMs,
  retryDelayMs,
  retryWaitMs,
} from "./retry";

/** What a `plan_limit_reached` refusal carries: the bound it met and the plan that lifts it. */
export interface PlanLimit {
  lever: string;
  plan: string;
  limit: number | null;
  suggested_plan: string | null;
}

/** What a `stream_truncated` refusal carries: the highest seq trimmed, and the head to go on from. */
export interface StreamTruncated {
  floor: number;
  head: number;
}

/** An org a `last_owner` refusal names: the team orgs the person is the last owner of. */
export type OwnedOrg = OwnedOrgRef;

export interface ErrorEnvelope {
  error: {
    code: string;
    message: string;
    request_id: string;
    plan_limit?: PlanLimit | null;
    stream?: StreamTruncated | null;
    last_owner?: LastOwnerDetail | null;
  };
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string | null;
  /** How long the server asked a retry to wait (its `Retry-After`), when it did. */
  readonly retryAfterMs: number | undefined;
  /** The bound a `plan_limit_reached` refusal met, when the envelope carried one. */
  readonly planLimit: PlanLimit | null;
  /** Where the stream goes on from, when a `stream_truncated` refusal carried it. */
  readonly stream: StreamTruncated | null;
  /** The orgs a `last_owner` refusal names; empty for every other refusal. */
  readonly lastOwnerOf: OwnedOrg[];

  constructor(
    status: number,
    code: string,
    message: string,
    requestId: string | null,
    retryAfterMs?: number,
    planLimit: PlanLimit | null = null,
    stream: StreamTruncated | null = null,
    lastOwnerOf: OwnedOrg[] = [],
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
    this.retryAfterMs = retryAfterMs;
    this.planLimit = planLimit;
    this.stream = stream;
    this.lastOwnerOf = lastOwnerOf;
  }
}

/** A call that did not finish, headers and body, within the client's deadline. */
export class RequestTimeout extends Error {
  readonly method: string;
  readonly path: string;
  readonly timeoutMs: number;

  constructor(method: string, path: string, timeoutMs: number) {
    super(`${method} ${path} did not finish within ${timeoutMs} ms`);
    this.name = "RequestTimeout";
    this.method = method;
    this.path = path;
    this.timeoutMs = timeoutMs;
  }
}

export interface ClientOptions {
  baseUrl: string;
  app: "portal" | "admin" | "cli" | "api";
  appVersion: string;
  /** Every call is abandoned after this many milliseconds; no call goes out without one. */
  timeoutMs: number;
  /** Extra attempts a retryable failure gets; 0 sends every call exactly once. */
  retryAttempts?: number;
  /** The wait before the first extra attempt; it doubles and carries jitter. */
  retryBaseDelayMs?: number;
  getToken: () => string | null;
  /** A 401 to the bearer the tab holds; the refused token is handed over. */
  onUnauthorized: (token: string) => void;
  fetchImpl?: typeof fetch;
}

export interface RequestOptions {
  idempotencyKey?: string;
  /** The version a write names as read, sent as the entity tag `If-Match` carries. */
  ifMatch?: number;
  token?: string | null;
  signal?: AbortSignal;
}

/** A body of bytes, sent as they are with their type, not as JSON. */
export class BytesBody {
  constructor(
    readonly bytes: Blob,
    readonly contentType: string,
  ) {}
}

export interface ApiClient {
  readonly baseUrl: string;
  request<T>(method: string, path: string, body?: unknown, options?: RequestOptions): Promise<T>;
  /** A file's bytes through the API, for a store that cannot take a form post; answers JSON. */
  putBytes<T>(path: string, bytes: Blob, contentType: string, options?: RequestOptions): Promise<T>;
  /** A file's bytes from the API, for a store that cannot sign a link. */
  getBlob(path: string, options?: RequestOptions): Promise<Blob>;
  get<T>(path: string, options?: RequestOptions): Promise<T>;
  post<T>(path: string, body?: unknown, options?: RequestOptions): Promise<T>;
  patch<T>(path: string, body?: unknown, options?: RequestOptions): Promise<T>;
  del<T>(path: string, options?: RequestOptions): Promise<T>;
  websocketUrl(path: string): string;
}

function isErrorEnvelope(value: unknown): value is ErrorEnvelope {
  if (typeof value !== "object" || value === null || !("error" in value)) return false;
  const error = (value as { error: unknown }).error;
  return typeof error === "object" && error !== null && "code" in error && "message" in error;
}

function isJson(contentType: string | null): boolean {
  return /\bjson\b/i.test(contentType ?? "");
}

/** The body as JSON, or undefined when it is not JSON after all. */
function parseJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}

function statusMessage(response: Response): string {
  return response.statusText || `HTTP ${response.status}`;
}

/**
 * Whether this failure can differ on a second attempt: the deadline, the
 * unavailable answer, and anything fetch threw instead of answering, which is
 * the connection failing before the request was decided. A refusal the server
 * made is a decision and comes back unchanged, so it is not retried.
 */
function isRetryableFailure(error: unknown): boolean {
  if (error instanceof RequestTimeout) return true;
  if (error instanceof ApiError) return isRetryableStatus(error.status);
  return error instanceof Error;
}

/** Resolves after the delay, or as soon as the caller gives up on the call. */
function wait(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const done = () => {
      clearTimeout(timer);
      signal?.removeEventListener("abort", done);
      resolve();
    };
    const timer = setTimeout(done, ms);
    signal?.addEventListener("abort", done, { once: true });
  });
}

export function createClient(options: ClientOptions): ApiClient {
  const fetchImpl = options.fetchImpl ?? fetch;
  const baseUrl = options.baseUrl.replace(/\/$/, "");
  const retryAttempts = options.retryAttempts ?? DEFAULT_RETRY_ATTEMPTS;
  const retryBaseDelayMs = options.retryBaseDelayMs ?? DEFAULT_RETRY_BASE_DELAY_MS;

  async function attempt<T>(
    method: string,
    path: string,
    body?: unknown,
    requestOptions: RequestOptions = {},
    asBlob = false,
  ): Promise<T> {
    const headers = new Headers({
      Accept: "application/json",
      "X-App": options.app,
      "X-App-Version": options.appVersion,
    });
    const token = requestOptions.token === undefined ? options.getToken() : requestOptions.token;
    if (token) headers.set("Authorization", `Bearer ${token}`);
    const raw = body instanceof BytesBody ? body : undefined;
    if (raw) headers.set("Content-Type", raw.contentType);
    else if (body !== undefined) headers.set("Content-Type", "application/json");
    if (requestOptions.idempotencyKey) headers.set("Idempotency-Key", requestOptions.idempotencyKey);
    if (requestOptions.ifMatch !== undefined) headers.set("If-Match", `"${requestOptions.ifMatch}"`);

    // One signal for the whole call: the deadline, and the caller's own
    // signal when it hands one over. Reading the body counts against the
    // deadline too, so the timer runs until the body is in.
    const controller = new AbortController();
    const timer = setTimeout(
      () => controller.abort(new RequestTimeout(method, path, options.timeoutMs)),
      options.timeoutMs,
    );
    const callerSignal = requestOptions.signal;
    const forwardAbort = () => controller.abort(callerSignal?.reason);
    if (callerSignal?.aborted) forwardAbort();
    else callerSignal?.addEventListener("abort", forwardAbort, { once: true });

    let response: Response;
    let text = "";
    let blob: Blob | undefined;
    try {
      response = await fetchImpl(`${baseUrl}${path}`, {
        method,
        headers,
        body: raw ? raw.bytes : body === undefined ? undefined : JSON.stringify(body),
        signal: controller.signal,
      });
      if (asBlob && response.ok) blob = await response.blob();
      else text = response.status === 204 ? "" : await response.text();
    } catch (error) {
      // A fetch aborted by the deadline reports the reason it was aborted with.
      throw controller.signal.aborted ? controller.signal.reason : error;
    } finally {
      clearTimeout(timer);
      callerSignal?.removeEventListener("abort", forwardAbort);
    }
    // The status and the content type decide before the body is read as
    // JSON: a 401 clears authentication whatever its body says, and a proxy's
    // HTML 502 or 504 is a typed error with the status, not a parse failure.
    const requestId = response.headers.get("x-request-id");
    // A refusal belongs to the bearer sent, not a replacement session or
    // a separate login credential used to choose an org.
    if (response.status === 401 && token && token === options.getToken()) options.onUnauthorized(token);
    const parsed = isJson(response.headers.get("content-type")) ? parseJson(text) : undefined;
    if (!response.ok) {
      const retryAfterMs = retryAfterHeaderMs(response.headers.get("retry-after"));
      if (isErrorEnvelope(parsed)) {
        throw new ApiError(
          response.status,
          parsed.error.code,
          parsed.error.message,
          parsed.error.request_id ?? requestId,
          retryAfterMs,
          parsed.error.plan_limit ?? null,
          parsed.error.stream ?? null,
          parsed.error.last_owner?.orgs ?? [],
        );
      }
      throw new ApiError(response.status, "unknown_error", statusMessage(response), requestId, retryAfterMs);
    }
    if (blob !== undefined) return blob as T;
    if (response.status === 204 || text === "") return undefined as T;
    if (parsed === undefined) {
      throw new ApiError(response.status, "not_json", `${method} ${path} answered with something other than JSON`, requestId);
    }
    return parsed as T;
  }

  /**
   * The app's one retry, and the only one: the query library's is off, so a
   * failing dependency sees these attempts and no multiple of them. A request
   * that may not be sent twice gets one attempt whatever the failure is.
   */
  async function request<T>(
    method: string,
    path: string,
    body?: unknown,
    requestOptions: RequestOptions = {},
    asBlob = false,
  ): Promise<T> {
    const bound = mayRetryRequest(method, requestOptions.idempotencyKey) ? retryAttempts : 0;
    for (let retry = 0; ; retry += 1) {
      try {
        return await attempt<T>(method, path, body, requestOptions, asBlob);
      } catch (error) {
        const spent = retry >= bound;
        if (spent || requestOptions.signal?.aborted || !isRetryableFailure(error)) throw error;
        const serverAsked = error instanceof ApiError ? error.retryAfterMs : undefined;
        await wait(retryWaitMs(retryDelayMs(retry + 1, retryBaseDelayMs), serverAsked), requestOptions.signal);
        // The caller gave up while this one waited; its reason is the answer.
        if (requestOptions.signal?.aborted) throw error;
      }
    }
  }

  return {
    baseUrl,
    request,
    get: (path, requestOptions) => request("GET", path, undefined, requestOptions),
    post: (path, body, requestOptions) => request("POST", path, body, requestOptions),
    patch: (path, body, requestOptions) => request("PATCH", path, body, requestOptions),
    del: (path, requestOptions) => request("DELETE", path, undefined, requestOptions),
    putBytes: (path, bytes, contentType, requestOptions) =>
      request("PUT", path, new BytesBody(bytes, contentType), requestOptions),
    getBlob: (path, requestOptions) => request<Blob>("GET", path, undefined, requestOptions, true),
    websocketUrl: (path) => baseUrl.replace(/^http/, "ws") + path,
  };
}
