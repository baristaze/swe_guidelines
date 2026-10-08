import { useFlag } from "../../queries/flags";
import { useStorageUsage } from "../../queries/media";
import { uploadsNotice, usageLine } from "./storageModel";

/** What the org keeps in the store, as one line, and whether new uploads
 * are paused; read-only. */
export function useStorageVm() {
  const usage = useStorageUsage();
  const uploadsOn = useFlag("media-uploads");
  return {
    loading: usage.isLoading,
    line: usage.data ? usageLine(usage.data.total_count, usage.data.total_size_bytes) : null,
    notice: uploadsNotice(uploadsOn),
  };
}
