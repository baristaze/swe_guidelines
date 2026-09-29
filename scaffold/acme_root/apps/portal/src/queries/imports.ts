import { useMutation, useQuery } from "@tanstack/react-query";
import type { AddFileRequest, FileView, ImportPageView, ImportView, StartImportRequest } from "../api";
import { api } from "../app/api";
import { keys } from "./keys";

/** The org's newest imports; the tasks page shows the one that matters now.
 * A push about any import refreshes it, since its key starts with the entity. */
export const RECENT_IMPORTS = 5;

export function useRecentImports(enabled = true) {
  return useQuery({
    queryKey: keys.imports.recent,
    enabled,
    queryFn: ({ signal }) => api.get<ImportPageView>(`/v1/tasks/imports?limit=${RECENT_IMPORTS}`, { signal }),
  });
}

/** The API calls an import's start is made of. The flow in
 * `features/imports/importFlow.ts` takes these as its effects, beside the
 * media calls that move the bytes. */
export const importCalls = {
  startFile: (body: AddFileRequest) =>
    api.post<FileView>("/v1/tasks/imports/files", body, { idempotencyKey: crypto.randomUUID() }),
  start: (body: StartImportRequest) =>
    api.post<ImportView>("/v1/tasks/imports", body, { idempotencyKey: crypto.randomUUID() }),
};

export function useResumeImport() {
  return useMutation({
    mutationFn: (id: string) => api.post<ImportView>(`/v1/tasks/imports/${id}/resume`),
  });
}
