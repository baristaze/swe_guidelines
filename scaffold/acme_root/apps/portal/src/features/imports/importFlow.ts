// The start of an import, with its effects handed in, so it runs in a test
// without React, the network, or a browser: the CSV file's upload begun on
// the API under the import's purpose, its bytes posted straight to the store
// with the form the API signed (or through the API where the store cannot
// take a form), the upload confirmed, and the import started naming it. The
// rows are read by the worker; this returns as soon as the import is
// accepted, and the page follows it by what the channel pushes.
import type { AddFileRequest, FileView, ImportView, IssuedUploadView, StartImportRequest } from "../../api";
import { storeForm, StoreRefused, type Picked } from "../attachments/transfer";

export const CSV_TYPE = "text/csv";

export interface ImportEffects {
  startFile(body: AddFileRequest): Promise<FileView>;
  issueUpload(fileId: string): Promise<IssuedUploadView>;
  postToStore(url: string, form: FormData): Promise<{ ok: boolean; status: number }>;
  putContent(fileId: string, bytes: Blob, contentType: string): Promise<FileView>;
  confirm(fileId: string): Promise<FileView>;
  start(body: StartImportRequest): Promise<ImportView>;
}

export async function startImport(file: Picked, effects: ImportEffects): Promise<ImportView> {
  // A browser names a CSV file's type as it likes (Windows says Excel); the
  // import takes the one type, and the name's extension is what the API holds.
  const started = await effects.startFile({ name: file.name, content_type: CSV_TYPE, size_bytes: file.size });
  const upload = await effects.issueUpload(started.id);
  if (upload.url === null) {
    await effects.putContent(started.id, file.bytes, CSV_TYPE);
  } else {
    const posted = await effects.postToStore(upload.url, storeForm(upload, file, CSV_TYPE));
    if (!posted.ok) throw new StoreRefused(posted.status);
  }
  const stored = await effects.confirm(started.id);
  return effects.start({ file_id: stored.id });
}
