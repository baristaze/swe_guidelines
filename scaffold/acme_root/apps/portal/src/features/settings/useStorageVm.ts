import { useStorageUsage } from "../../queries/media";
import { usageLine } from "./storageModel";

/** What the org keeps in the store, as one line; read-only. */
export function useStorageVm() {
  const usage = useStorageUsage();
  return {
    loading: usage.isLoading,
    line: usage.data ? usageLine(usage.data.total_count, usage.data.total_size_bytes) : null,
  };
}
