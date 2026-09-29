import { useQuery } from "@tanstack/react-query";
import type { StorageUsageView } from "../api";
import { api } from "../app/api";
import { keys } from "./keys";

/** What the org keeps in the store: how many files, and their bytes. */
export function useStorageUsage(enabled = true) {
  return useQuery({
    queryKey: keys.files.usage,
    enabled,
    queryFn: ({ signal }) => api.get<StorageUsageView>("/v1/media/usage", { signal }),
  });
}
