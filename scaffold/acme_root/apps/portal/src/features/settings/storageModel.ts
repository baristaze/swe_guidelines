// Pure: how the org's storage reads as one line. Values in, values out.

/** Bytes as a person reads them: B under a kilobyte, then KB and MB. */
export function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** "3 files, 1.2 MB": the storage line the settings page shows. */
export function usageLine(count: number, bytes: number): string {
  return `${count} ${count === 1 ? "file" : "files"}, ${humanSize(bytes)}`;
}
