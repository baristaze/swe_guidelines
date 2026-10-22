import { describe, expect, it } from "vitest";
import { createClient } from "./client";
import { uploadFile, UploadStopped } from "./store";

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
  const client = createClient({
    baseUrl: "https://api.test",
    app: "portal",
    appVersion: "portal@test",
    timeoutMs: 1000,
    getToken: () => "ses_1",
    onUnauthorized: () => undefined,
    fetchImpl,
  });
  return { client, api, parts, fetchImpl };
}

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
});
