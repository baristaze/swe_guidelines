import { describe, expect, it, vi } from "vitest";
import type { FileView, ImportView, IssuedUploadView } from "../../api";
import { StoreRefused } from "../attachments/transfer";
import { CSV_TYPE, startImport, type ImportEffects } from "./importFlow";

const FILE: FileView = {
  id: "f1",
  name: "tasks.csv",
  extension: "csv",
  content_type: CSV_TYPE,
  size_bytes: 12,
  purpose: "task_import",
  subject_id: null,
  status: "pending",
  created_at: "2026-09-25T10:00:00Z",
  created_by: "u1",
  deleted_at: null,
};

const STARTED: ImportView = {
  id: "i1",
  file_id: "f1",
  status: "running",
  total: null,
  cursor: 0,
  created: 0,
  skipped: 0,
  row_errors: [],
  park_reason: null,
  fail_reason: null,
  created_at: "2026-09-25T10:00:00Z",
  updated_at: "2026-09-25T10:00:00Z",
  finished_at: null,
  created_by: "u1",
};

// Windows names a CSV file as Excel's; the import sends the one type it takes.
const picked = { name: "tasks.csv", type: "application/vnd.ms-excel", size: 12, bytes: new Blob(["title\nShip\n"]) };

function effectsFor(form: IssuedUploadView, storeStatus = 204) {
  const calls: string[] = [];
  const effects: ImportEffects = {
    startFile: vi.fn(async (body) => {
      calls.push(`file ${body.name} ${body.content_type} ${body.size_bytes}`);
      return FILE;
    }),
    issueUpload: vi.fn(async (fileId) => {
      calls.push(`form ${fileId}`);
      return form;
    }),
    postToStore: vi.fn(async (url) => {
      calls.push(`post ${url}`);
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
    start: vi.fn(async (body) => {
      calls.push(`import ${body.file_id}`);
      return STARTED;
    }),
  };
  return { effects, calls };
}

const signed: IssuedUploadView = {
  url: "https://store.example.test/bucket",
  fields: [{ name: "key", value: "org/media/task_import/f1" }],
  expires_at: "2026-09-25T10:15:00Z",
};

describe("startImport", () => {
  it("uploads the file to the store, confirms it, and starts its import", async () => {
    const { effects, calls } = effectsFor(signed);
    expect(await startImport(picked, effects)).toEqual(STARTED);
    expect(calls).toEqual([
      "file tasks.csv text/csv 12",
      "form f1",
      "post https://store.example.test/bucket",
      "confirm f1",
      "import f1",
    ]);
  });

  it("sends the bytes through the API where the store takes no form", async () => {
    const { effects, calls } = effectsFor({ url: null, fields: [], expires_at: signed.expires_at });
    await startImport(picked, effects);
    expect(calls).toContain("put f1 text/csv");
    expect(calls).not.toContain("post https://store.example.test/bucket");
  });

  it("starts nothing when the store refuses the file", async () => {
    const { effects, calls } = effectsFor(signed, 400);
    await expect(startImport(picked, effects)).rejects.toBeInstanceOf(StoreRefused);
    expect(calls).not.toContain("import f1");
  });
});
