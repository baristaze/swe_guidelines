import { describe, expect, it } from "vitest";
import type { FileView } from "../../api";
import { attachmentRow, contentTypeOf, extensionOf, humanSize, previewKind, usageLine } from "./attachmentsModel";

function file(overrides: Partial<FileView> = {}): FileView {
  return {
    id: "f1",
    name: "Quarterly plan.docx",
    extension: "docx",
    content_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    size_bytes: 1536,
    purpose: "task_attachment",
    subject_id: "t1",
    status: "stored",
    created_at: "2026-09-22T10:00:00Z",
    created_by: "u1",
    deleted_at: null,
    ...overrides,
  };
}

describe("attachments model", () => {
  it("says a size in the unit a person reads", () => {
    expect([0, 1023, 1024, 1536, 25 * 1024 * 1024].map(humanSize)).toEqual([
      "0 B",
      "1023 B",
      "1.0 KB",
      "1.5 KB",
      "25.0 MB",
    ]);
  });

  it("keeps the browser's type and guesses one only when the browser named none", () => {
    expect(contentTypeOf("notes.md", "")).toBe("text/markdown");
    expect(contentTypeOf("plan.pdf", "application/pdf")).toBe("application/pdf");
    expect(contentTypeOf("blob.nokind", "")).toBe("application/octet-stream");
    expect(extensionOf(".hidden")).toBe("");
    expect(extensionOf("a.TAR.GZ")).toBe("gz");
  });

  it("shows a row with the name, the size, and a short type, the whole type kept for the title", () => {
    expect(attachmentRow(file())).toEqual({
      id: "f1",
      name: "Quarterly plan.docx",
      size: "1.5 KB",
      kind: "DOCX",
      contentType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      preview: null,
    });
    expect(attachmentRow(file({ extension: "", content_type: "text/plain" })).kind).toBe("plain");
  });

  it("previews an image, a video, a sound, and a PDF, and nothing else", () => {
    expect(["image/png", "video/mp4", "audio/webm", "application/pdf", "text/plain", "application/zip"].map(previewKind)).toEqual([
      "image",
      "video",
      "audio",
      "pdf",
      null,
      null,
    ]);
  });

  it("says the storage line in the singular and the plural", () => {
    expect(usageLine(1, 10)).toBe("1 file, 10 B");
    expect(usageLine(3, 3 * 1024 * 1024)).toBe("3 files, 3.0 MB");
  });
});
