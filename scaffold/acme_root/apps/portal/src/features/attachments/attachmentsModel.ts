// Pure: how a file is shown, and what type a file the browser could not name
// is sent as. Values in, values out; the server decides what it accepts.
import type { FileView } from "../../api";

/** Bytes as a person reads them: B under a kilobyte, then KB and MB. */
export function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// The types a browser often leaves empty; the server holds the name's
// extension to the type, so the guess follows the same table.
const BY_EXTENSION: Readonly<Record<string, string>> = {
  md: "text/markdown",
  markdown: "text/markdown",
  csv: "text/csv",
  log: "text/plain",
  txt: "text/plain",
  json: "application/json",
  pdf: "application/pdf",
  zip: "application/zip",
  webp: "image/webp",
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  pptx: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
};

export function extensionOf(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot > 0 && dot < name.length - 1 ? name.slice(dot + 1).toLowerCase() : "";
}

/** The type a file is sent as: the browser's when it named one, else the one
 * its extension carries, else plain bytes, which the server will refuse
 * with a sentence that says why. */
export function contentTypeOf(name: string, browserType: string): string {
  if (browserType) return browserType;
  return BY_EXTENSION[extensionOf(name)] ?? "application/octet-stream";
}

/** How a file is shown in the page: an image as a thumbnail, a video and a
 * sound with the browser's player, a PDF in a frame. Anything else is
 * download-only. */
export type PreviewKind = "image" | "video" | "audio" | "pdf";

export function previewKind(contentType: string): PreviewKind | null {
  if (contentType.startsWith("image/")) return "image";
  if (contentType.startsWith("video/")) return "video";
  if (contentType.startsWith("audio/")) return "audio";
  if (contentType === "application/pdf") return "pdf";
  return null;
}

export interface AttachmentRow {
  id: string;
  name: string;
  size: string;
  /** Short: the extension in capitals, or the type's second half. */
  kind: string;
  /** The whole type, for the title. */
  contentType: string;
  preview: PreviewKind | null;
}

export function attachmentRow(file: FileView): AttachmentRow {
  const kind = file.extension ? file.extension.toUpperCase() : (file.content_type.split("/")[1] ?? "file");
  return {
    id: file.id,
    name: file.name,
    size: humanSize(file.size_bytes),
    kind,
    contentType: file.content_type,
    preview: previewKind(file.content_type),
  };
}

/** "3 files, 1.2 MB": the storage line the settings page shows. */
export function usageLine(count: number, bytes: number): string {
  return `${count} ${count === 1 ? "file" : "files"}, ${humanSize(bytes)}`;
}
