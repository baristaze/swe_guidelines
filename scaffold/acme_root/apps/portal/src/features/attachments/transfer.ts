// The two flows that move a file's bytes, with their effects handed in, so
// each runs in a test without React, the network, or a browser download.
//
// An upload starts on the API, posts the bytes straight to the store with the
// form the API signed (every field in order, then the file, last, named
// `file`), and is confirmed on the API, which looks for the object before it
// says stored. When the store cannot take a post (the form has no URL), the
// bytes go through the API instead, held to the same bounds. A download
// follows the link the API signed, or reads the bytes through the API when
// there is no link.
import type { AddFileRequest, FileView, IssuedDownloadView, IssuedUploadView } from "../../api";
import { contentTypeOf } from "./attachmentsModel";

/** The store answered, and not with a success: the form refused the body
 * (too large, another type) or the link has expired. */
export class StoreRefused extends Error {
  constructor(readonly status: number) {
    super(
      status === 400
        ? "The store refused the file: it is larger than it said."
        : `The store refused the transfer (HTTP ${status}).`,
    );
    this.name = "StoreRefused";
  }
}

export interface UploadEffects {
  start(taskId: string, body: AddFileRequest): Promise<FileView>;
  issueUpload(fileId: string): Promise<IssuedUploadView>;
  postToStore(url: string, form: FormData): Promise<{ ok: boolean; status: number }>;
  putContent(fileId: string, bytes: Blob, contentType: string): Promise<FileView>;
  confirm(fileId: string): Promise<FileView>;
}

export interface Picked {
  name: string;
  type: string;
  size: number;
  bytes: Blob;
}

export function storeForm(upload: IssuedUploadView, file: Picked, contentType: string): FormData {
  const form = new FormData();
  for (const field of upload.fields) form.append(field.name, field.value);
  // The file is the last field: the store reads the fields before it and
  // ignores any after.
  form.append("file", new Blob([file.bytes], { type: contentType }), file.name);
  return form;
}

export async function uploadAttachment(taskId: string, file: Picked, effects: UploadEffects): Promise<FileView> {
  const contentType = contentTypeOf(file.name, file.type);
  const started = await effects.start(taskId, { name: file.name, content_type: contentType, size_bytes: file.size });
  const upload = await effects.issueUpload(started.id);
  if (upload.url === null) {
    await effects.putContent(started.id, file.bytes, contentType);
  } else {
    const posted = await effects.postToStore(upload.url, storeForm(upload, file, contentType));
    if (!posted.ok) throw new StoreRefused(posted.status);
  }
  return effects.confirm(started.id);
}

export interface DownloadEffects {
  issueDownload(fileId: string): Promise<IssuedDownloadView>;
  fetchFromStore(url: string): Promise<{ ok: boolean; status: number; blob(): Promise<Blob> }>;
  getContent(fileId: string): Promise<Blob>;
  save(blob: Blob, name: string): void;
}

/** The bytes are fetched and saved under the file's own name: a link to
 * another origin cannot name the file it saves, and the store names the
 * object by its id. */
export async function downloadAttachment(file: FileView, effects: DownloadEffects): Promise<void> {
  const link = await effects.issueDownload(file.id);
  let blob: Blob;
  if (link.url === null) {
    blob = await effects.getContent(file.id);
  } else {
    const fetched = await effects.fetchFromStore(link.url);
    if (!fetched.ok) throw new StoreRefused(fetched.status);
    blob = await fetched.blob();
  }
  effects.save(blob, file.name);
}

/** Hands a blob to the browser as a download under `name`. */
export function saveBlob(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = name;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
