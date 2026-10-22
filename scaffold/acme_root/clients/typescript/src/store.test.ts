import { describe, expect, it, vi } from "vitest";
import { createClient, RequestTimeout } from "./client";
import { TRANSFER_TIMEOUT_MS, uploadFile, UploadStopped } from "./store";

const FILE = {
  id: "f1",
  name: "spec.pdf",
  extension: "pdf",
  content_type: "application/pdf",
  size_bytes: 17,
  purpose: "upload",
  subject_id: null,
  status: "pending",
  created_at: "2026-09-18T12:00:00Z",
  created_by: "u1",
  deleted_at: null,
};

function issued(...parts: [number, number, number][]) {
  return {
    upload_id: "u-1",
    parts: parts.map(([number, offset, size_bytes]) => ({
      number,
      offset,
      size_bytes,
      url: `https://store.test/p${number}?sig=x`,
    })),
    remaining: parts.length,
    expires_at: "2026-09-18T12:15:00Z",
  };
}

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });
}

/** The API in turn: the start, then each issue, then the confirm; and the store. */
function world(issues: ReturnType<typeof issued>[], store: (url: string, init: RequestInit) => Response) {
  const api: { method: string; url: string; body: unknown }[] = [];
  const parts: { url: string; body: BodyInit | null | undefined }[] = [];
  const fetchImpl = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = String(input);
    if (url.startsWith("https://store.test/")) {
      parts.push({ url, body: init?.body });
      return store(url, init ?? {});
    }
    api.push({ method: init?.method ?? "GET", url, body: init?.body });
    if (url.endsWith("/v1/media/files") || url.endsWith("/v1/media/files/f1")) return json(FILE);
    if (url.endsWith("/upload")) return json(issues.shift());
    if (url.endsWith("/confirm")) return json({ ...FILE, status: "stored" });
    throw new Error(url);
  };
  return { client: clientOver(fetchImpl), api, parts, fetchImpl };
}

function clientOver(fetchImpl: typeof fetch) {
  return createClient({
    baseUrl: "https://api.test",
    app: "portal",
    appVersion: "portal@test",
    timeoutMs: 1000,
    getToken: () => "ses_1",
    onUnauthorized: () => undefined,
    fetchImpl,
  });
}

/** The parts of the 17-byte file: number, offset, size. */
const LAYOUT: [number, number, number][] = [
  [1, 0, 6],
  [2, 6, 6],
  [3, 12, 5],
];

/** An API that signs only the parts its store does not hold, as the service
 * does, and a store that holds each part it is sent. A request that `cut`
 * answers, by its URL, ends as `cut` says instead: a connection that drops, a
 * part that never answers. */
function resumable(cut: (url: string, signal: AbortSignal) => Promise<Response> | undefined) {
  const held = new Set<number>();
  const puts: number[] = [];
  const fetchImpl = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = String(input);
    const part = /^https:\/\/store\.test\/p(\d+)\?/.exec(url);
    if (part) puts.push(Number(part[1]));
    const ending = cut(url, init!.signal!);
    if (ending) return ending;
    if (part) {
      held.add(Number(part[1]));
      return new Response(null);
    }
    if (url.endsWith("/v1/media/files") || url.endsWith("/v1/media/files/f1")) return json(FILE);
    if (url.endsWith("/upload")) return json(issued(...LAYOUT.filter(([number]) => !held.has(number))));
    if (url.endsWith("/confirm")) return json({ ...FILE, status: "stored" });
    throw new Error(url);
  };
  return { client: clientOver(fetchImpl), puts, fetchImpl };
}

const dropped = () => Promise.reject(new TypeError("Failed to fetch"));

describe("an upload", () => {
  it("sends each part straight to the store, and none of the file to the API", async () => {
    const spec = new File(["%PDF-1.7 the spec"], "spec.pdf", { type: "application/pdf" });
    const { client, api, parts, fetchImpl } = world([issued([1, 0, 10], [2, 10, 7]), issued()], () => new Response(null));
    const stored = await uploadFile(client, spec, { fetchImpl });
    expect(stored.status).toBe("stored");
    expect(api.map((r) => `${r.method} ${r.url.replace("https://api.test", "")}`)).toEqual([
      "POST /v1/media/files",
      "POST /v1/media/files/f1/upload",
      "POST /v1/media/files/f1/upload",
      "POST /v1/media/files/f1/confirm",
    ]);
    expect(JSON.parse(String(api[0]!.body))).toEqual({ name: "spec.pdf", content_type: "application/pdf", size_bytes: 17 });
    expect(api.slice(1).every((r) => r.body === undefined)).toBe(true);
    const sent = await Promise.all(parts.map(async (p) => [p.url.split("?")[0], await (p.body as Blob).text()]));
    expect(sent).toEqual([
      ["https://store.test/p1", "%PDF-1.7 t"],
      ["https://store.test/p2", "he spec"],
    ]);
  });

  it("goes on with a started upload, and stops on a part the store refuses or loses", async () => {
    const spec = new File(["%PDF-1.7 the spec"], "spec.pdf", { type: "application/pdf" });
    const refusing = world([issued([2, 10, 7])], () => new Response("SignatureDoesNotMatch", { status: 403 }));
    const refused = uploadFile(refusing.client, spec, { fileId: "f1", fetchImpl: refusing.fetchImpl });
    await expect(refused).rejects.toBeInstanceOf(UploadStopped);
    await expect(refused).rejects.toMatchObject({ fileId: "f1" });
    expect(refusing.api[0]!.url).toBe("https://api.test/v1/media/files/f1");
    const losing = world([issued([2, 10, 7]), issued([2, 10, 7])], () => new Response(null));
    await expect(uploadFile(losing.client, spec, { fileId: "f1", fetchImpl: losing.fetchImpl })).rejects.toThrow(
      "kept no part 2",
    );
    expect(losing.parts).toHaveLength(1);
    const other = new File(["short"], "spec.pdf");
    await expect(uploadFile(losing.client, other, { fileId: "f1", fetchImpl: losing.fetchImpl })).rejects.toThrow(
      "its upload is of 17",
    );
  });

  it("stops under the file's id when its connection drops, and going on sends only the missing parts", async () => {
    const spec = new File(["%PDF-1.7 the spec"], "spec.pdf", { type: "application/pdf" });
    let down = true;
    const w = resumable((url) => (down && url.startsWith("https://store.test/p2") ? dropped() : undefined));
    const stopped = await uploadFile(w.client, spec, { fetchImpl: w.fetchImpl }).catch((error: unknown) => error);
    expect(stopped).toBeInstanceOf(UploadStopped);
    expect(stopped).toMatchObject({ fileId: "f1", cause: expect.any(TypeError) });
    expect(w.puts).toEqual([1, 2]);
    down = false;
    const stored = await uploadFile(w.client, spec, { fileId: (stopped as UploadStopped).fileId, fetchImpl: w.fetchImpl });
    expect(stored.status).toBe("stored");
    expect(w.puts).toEqual([1, 2, 2, 3]);
  });

  it("stops under the file's id when a part times out, and going on sends only the missing parts", async () => {
    vi.useFakeTimers();
    try {
      const spec = new File(["%PDF-1.7 the spec"], "spec.pdf", { type: "application/pdf" });
      let down = true;
      let reached = () => {};
      const hanging = new Promise<void>((resolve) => (reached = resolve));
      const w = resumable((url, signal) => {
        if (!down || !url.startsWith("https://store.test/p2")) return undefined;
        reached();
        // A browser's fetch rejects an aborted call with an AbortError.
        return new Promise<Response>((_, reject) =>
          signal.addEventListener("abort", () => reject(new DOMException("This operation was aborted", "AbortError"))),
        );
      });
      const outcome = uploadFile(w.client, spec, { fetchImpl: w.fetchImpl }).catch((error: unknown) => error);
      await hanging;
      await vi.advanceTimersByTimeAsync(TRANSFER_TIMEOUT_MS);
      const stopped = await outcome;
      expect(stopped).toBeInstanceOf(UploadStopped);
      expect(stopped).toMatchObject({ fileId: "f1", cause: expect.any(RequestTimeout) });
      expect((stopped as Error).message).toContain("/p2 did not finish");
      expect((stopped as Error).message).not.toContain("sig=");
      expect(w.puts).toEqual([1, 2]);
      down = false;
      const stored = await uploadFile(w.client, spec, { fileId: "f1", fetchImpl: w.fetchImpl });
      expect(stored.status).toBe("stored");
      expect(w.puts).toEqual([1, 2, 2, 3]);
    } finally {
      vi.useRealTimers();
    }
  });

  it.each(["/upload", "/confirm"])("stops under the file's id when the API's %s drops, and goes on", async (call) => {
    const spec = new File(["%PDF-1.7 the spec"], "spec.pdf", { type: "application/pdf" });
    let down = true;
    const w = resumable((url) => (down && url.endsWith(call) ? dropped() : undefined));
    const stopped = await uploadFile(w.client, spec, { fetchImpl: w.fetchImpl }).catch((error: unknown) => error);
    expect(stopped).toMatchObject({ name: "UploadStopped", fileId: "f1", cause: expect.any(TypeError) });
    down = false;
    expect((await uploadFile(w.client, spec, { fileId: "f1", fetchImpl: w.fetchImpl })).status).toBe("stored");
    expect(w.puts).toEqual([1, 2, 3]);
  });

  it("starts nothing to go on with when the start itself fails", async () => {
    const spec = new File(["%PDF-1.7 the spec"], "spec.pdf", { type: "application/pdf" });
    const w = resumable((url) => (url.endsWith("/v1/media/files") ? dropped() : undefined));
    await expect(uploadFile(w.client, spec, { fetchImpl: w.fetchImpl })).rejects.toBeInstanceOf(TypeError);
  });
});
