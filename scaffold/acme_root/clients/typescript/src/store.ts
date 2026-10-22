// The object store, reached through a URL the API signed and handed over: a
// part an upload PUTs, a link a download follows. It is not the API, so
// nothing of the API client goes with it: no bearer, no app headers, no
// retry. The URL is the credential, good for one part or one file for a few
// minutes. A file's bytes never cross the API.

import { RequestTimeout, type ApiClient } from "./client";
import type { FileView, IssuedUploadView } from "./types/media";

/** A transfer moves a part of a file, not a JSON body, so its deadline is its
 * own: long enough for a part on a slow link, and well inside the fifteen
 * minutes its URL lives. */
export const TRANSFER_TIMEOUT_MS = 10 * 60 * 1000;

export async function storeFetch(
  url: string,
  init: RequestInit = {},
  fetchImpl: typeof fetch = fetch,
): Promise<Response> {
  const controller = new AbortController();
  // The deadline names the object, never the signature its query carries.
  const where = url.split("?")[0] ?? url;
  const timer = setTimeout(
    () => controller.abort(new RequestTimeout(init.method ?? "GET", where, TRANSFER_TIMEOUT_MS)),
    TRANSFER_TIMEOUT_MS,
  );
  try {
    return await fetchImpl(url, { ...init, signal: controller.signal });
  } catch (error) {
    // A transfer cut off by its deadline reports the deadline, not the abort.
    throw controller.signal.aborted ? controller.signal.reason : error;
  } finally {
    clearTimeout(timer);
  }
}

/** An upload that started and did not finish: a part refused, lost, cut off,
 * or out of time, or an API call that failed on the way. Asking `uploadFile`
 * again with `fileId` sends what the store does not hold. The failure under
 * it is its `cause`. */
export class UploadStopped extends Error {
  constructor(
    readonly fileId: string,
    message: string,
    options?: ErrorOptions,
  ) {
    super(message, options);
    this.name = "UploadStopped";
  }
}

export interface UploadOptions {
  /** A started upload to go on with, rather than a new one. */
  fileId?: string;
  /** The type the file goes up as, when the browser names none. */
  contentType?: string;
  fetchImpl?: typeof fetch;
}

/**
 * A file the org keeps, sent the one way a file is sent: a multipart upload
 * whose parts go straight to the store, each a slice of the file PUT to the
 * URL the API signed for its length. The page holds one slice at a time,
 * whatever the file's size. An upload cut off goes on with `fileId`: the
 * API signs only the parts the store does not hold. Each part is sent once a
 * call, so a store that keeps losing one stops the call.
 */
export async function uploadFile(api: ApiClient, file: File, options: UploadOptions = {}): Promise<FileView> {
  const fetchImpl = options.fetchImpl ?? fetch;
  // Known once the upload exists: from then on every failure stops it under
  // this id, so the page can go on with it rather than start over.
  let fileId = options.fileId;
  try {
    const started =
      fileId === undefined
        ? await api.post<FileView>(
            "/v1/media/files",
            { name: file.name, content_type: options.contentType ?? file.type, size_bytes: file.size },
            { idempotencyKey: crypto.randomUUID() },
          )
        : await api.get<FileView>(`/v1/media/files/${fileId}`);
    fileId = started.id;
    if (started.size_bytes !== file.size) {
      throw new UploadStopped(started.id, `${file.name} is ${file.size} bytes; its upload is of ${started.size_bytes}`);
    }
    const sent = new Set<number>();
    for (;;) {
      const issued = await api.post<IssuedUploadView>(`/v1/media/files/${started.id}/upload`);
      if (issued.parts.length === 0) break;
      for (const part of issued.parts) {
        if (sent.has(part.number)) throw new UploadStopped(started.id, `the store kept no part ${part.number}`);
        const body = file.slice(part.offset, part.offset + part.size_bytes);
        const put = await storeFetch(part.url, { method: "PUT", body }, fetchImpl).catch((error: unknown) => {
          throw new UploadStopped(started.id, `part ${part.number} did not reach the store: ${reason(error)}`, {
            cause: error,
          });
        });
        if (!put.ok) throw new UploadStopped(started.id, `the store refused part ${part.number} (${put.status})`);
        sent.add(part.number);
      }
    }
    return await api.post<FileView>(`/v1/media/files/${started.id}/confirm`);
  } catch (error) {
    if (fileId === undefined || error instanceof UploadStopped) throw error;
    throw new UploadStopped(fileId, `the upload of ${file.name} stopped: ${reason(error)}`, { cause: error });
  }
}

function reason(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
