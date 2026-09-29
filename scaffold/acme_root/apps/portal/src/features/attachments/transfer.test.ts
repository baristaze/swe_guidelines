import { describe, expect, it, vi } from "vitest";
import type { FileView, IssuedDownloadView, IssuedUploadView } from "../../api";
import { downloadAttachment, StoreRefused, uploadAttachment, type DownloadEffects, type UploadEffects } from "./transfer";

const FILE: FileView = {
  id: "f1",
  name: "plan.pdf",
  extension: "pdf",
  content_type: "application/pdf",
  size_bytes: 5,
  purpose: "task_attachment",
  subject_id: "t1",
  status: "pending",
  created_at: "2026-09-22T10:00:00Z",
  created_by: "u1",
  deleted_at: null,
};

const picked = { name: "plan.pdf", type: "application/pdf", size: 5, bytes: new Blob(["%PDF!"]) };

function uploads(form: IssuedUploadView, storeStatus = 204) {
  const calls: string[] = [];
  const posted: FormData[] = [];
  const effects: UploadEffects = {
    start: vi.fn(async (taskId, body) => {
      calls.push(`start ${taskId} ${body.name} ${body.content_type} ${body.size_bytes}`);
      return FILE;
    }),
    issueUpload: vi.fn(async (fileId) => {
      calls.push(`form ${fileId}`);
      return form;
    }),
    postToStore: vi.fn(async (url, body) => {
      calls.push(`post ${url}`);
      posted.push(body);
      return { ok: storeStatus < 300, status: storeStatus };
    }),
    putContent: vi.fn(async (fileId, _bytes, contentType) => {
      calls.push(`put ${fileId} ${contentType}`);
      return FILE;
    }),
    confirm: vi.fn(async (fileId) => {
      calls.push(`confirm ${fileId}`);
      return { ...FILE, status: "stored" as const };
    }),
  };
  return { effects, calls, posted };
}

const signed: IssuedUploadView = {
  url: "https://store.example.test/bucket",
  fields: [
    { name: "key", value: "org/media/task_attachment/f1" },
    { name: "Content-Type", value: "application/pdf" },
    { name: "policy", value: "p" },
  ],
  expires_at: "2026-09-22T10:15:00Z",
};

describe("uploadAttachment", () => {
  it("starts, posts every field in order and the file last to the store, then confirms", async () => {
    const { effects, calls, posted } = uploads(signed);
    const stored = await uploadAttachment("t1", picked, effects);
    expect(stored.status).toBe("stored");
    expect(calls).toEqual([
      "start t1 plan.pdf application/pdf 5",
      "form f1",
      "post https://store.example.test/bucket",
      "confirm f1",
    ]);
    const names = [...posted[0]!.keys()];
    expect(names).toEqual(["key", "Content-Type", "policy", "file"]);
    expect(effects.putContent).not.toHaveBeenCalled();
  });

  it("moves the bytes through the API when the store cannot take a post", async () => {
    const { effects, calls } = uploads({ url: null, fields: [], expires_at: signed.expires_at });
    await uploadAttachment("t1", picked, effects);
    expect(calls).toEqual(["start t1 plan.pdf application/pdf 5", "form f1", "put f1 application/pdf", "confirm f1"]);
  });

  it("stops at a store that refused the body and never confirms it", async () => {
    const { effects } = uploads(signed, 400);
    await expect(uploadAttachment("t1", picked, effects)).rejects.toBeInstanceOf(StoreRefused);
    expect(effects.confirm).not.toHaveBeenCalled();
  });

  it("names a type the browser left empty from the extension", async () => {
    const { effects, calls } = uploads(signed);
    await uploadAttachment("t1", { ...picked, name: "notes.md", type: "" }, effects);
    expect(calls[0]).toBe("start t1 notes.md text/markdown 5");
  });
});

function downloads(link: IssuedDownloadView, storeStatus = 200) {
  const saved: [Blob, string][] = [];
  const effects: DownloadEffects = {
    issueDownload: vi.fn(async () => link),
    fetchFromStore: vi.fn(async () => ({
      ok: storeStatus < 300,
      status: storeStatus,
      blob: async () => new Blob(["from the store"]),
    })),
    getContent: vi.fn(async () => new Blob(["through the api"])),
    save: (blob, name) => void saved.push([blob, name]),
  };
  return { effects, saved };
}

describe("downloadAttachment", () => {
  it("follows the signed link and saves under the file's own name", async () => {
    const { effects, saved } = downloads({ url: "https://store.example.test/o", expires_at: signed.expires_at });
    await downloadAttachment(FILE, effects);
    expect(await saved[0]![0].text()).toBe("from the store");
    expect(saved[0]![1]).toBe("plan.pdf");
    expect(effects.getContent).not.toHaveBeenCalled();
  });

  it("reads the bytes through the API when there is no link", async () => {
    const { effects, saved } = downloads({ url: null, expires_at: signed.expires_at });
    await downloadAttachment(FILE, effects);
    expect(await saved[0]![0].text()).toBe("through the api");
  });

  it("says a link the store refused and saves nothing", async () => {
    const { effects, saved } = downloads({ url: "https://store.example.test/o", expires_at: signed.expires_at }, 403);
    await expect(downloadAttachment(FILE, effects)).rejects.toThrow("HTTP 403");
    expect(saved).toEqual([]);
  });
});
