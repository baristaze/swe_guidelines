import { useInfiniteQuery, useMutation, useQuery } from "@tanstack/react-query";
import type {
  AddTaskRequest,
  BulkTasksRequest,
  BulkTasksView,
  MoveTaskRequest,
  RestoreTaskRequest,
  TaskCountView,
  TaskPageView,
  TaskScope,
  TaskStatus,
  TaskView,
  UpdateTaskRequest,
} from "../api";
import { api } from "../app/api";
import { keys } from "./keys";
import { DONE_PAGE_SIZE, OPEN_PAGE_SIZE } from "./taskPlacement";

export { DONE_PAGE_SIZE, OPEN_PAGE_SIZE } from "./taskPlacement";

/** A list a page at a time; the next page is the last page's cursor, and the
 * server says when there is none. Both lists page the same way; only the
 * page size differs, since the open list is the one on screen whole. */
function useTaskPages(status: TaskStatus, scope: TaskScope, pageSize: number) {
  return useInfiniteQuery({
    queryKey: status === "open" ? keys.tasks.open(scope) : keys.tasks.done(scope),
    initialPageParam: null as string | null,
    getNextPageParam: (last: TaskPageView) => last.next_cursor,
    // The signal is passed on: an invalidation cancels the refetch it
    // supersedes, and without it the cancelled request still runs to
    // completion. A replay hands the router a page of records at a time.
    queryFn: ({ pageParam, signal }) => {
      const cursor = pageParam ? `&cursor=${encodeURIComponent(pageParam)}` : "";
      return api.get<TaskPageView>(
        `/v1/tasks?status=${status}&scope=${scope}&limit=${pageSize}${cursor}`,
        { signal },
      );
    },
  });
}

/** One task, read outside any list: a reminder push names only the id. */
export function fetchTask(id: string): Promise<TaskView> {
  return api.get<TaskView>(`/v1/tasks/${encodeURIComponent(id)}`);
}

export function useOpenTasks(scope: TaskScope) {
  return useTaskPages("open", scope, OPEN_PAGE_SIZE);
}

export function useDoneTasks(scope: TaskScope) {
  return useTaskPages("done", scope, DONE_PAGE_SIZE);
}

// Mutations only talk to the server; the list's view model owns the cache edits
// and the motion around them. Every write names the version of the task the
// view model holds, in `If-Match` or, on the move, `expected_version`: the
// server refuses a write over a task that changed since (412
// `precondition_failed`), and the view model reloads the list.

export function useCreateTask() {
  return useMutation({
    mutationFn: (body: AddTaskRequest) =>
      api.post<TaskView>("/v1/tasks", body, { idempotencyKey: crypto.randomUUID() }),
  });
}

export function useUpdateTask() {
  return useMutation({
    mutationFn: ({ id, body, version }: { id: string; body: UpdateTaskRequest; version: number }) =>
      api.patch<TaskView>(`/v1/tasks/${id}`, body, { ifMatch: version }),
  });
}

export function useMoveTask() {
  return useMutation({
    mutationFn: ({ id, afterId, version }: { id: string; afterId: string | null; version: number }) =>
      api.post<TaskView>(`/v1/tasks/${id}/move`, {
        after_id: afterId,
        expected_version: version,
      } satisfies MoveTaskRequest),
  });
}

export function useDeleteTask() {
  return useMutation({
    mutationFn: ({ id, version }: { id: string; version: number }) =>
      api.del<TaskView>(`/v1/tasks/${id}`, { ifMatch: version }),
  });
}

/** The archive's first page, newest first: read only when the person opens
 * it. A task push refreshes it by the convention, as it does the lists. */
export const ARCHIVED_PAGE_SIZE = 50;

export function useArchivedTasks(scope: TaskScope, enabled: boolean) {
  return useQuery({
    queryKey: keys.tasks.archived(scope),
    enabled,
    queryFn: ({ signal }) =>
      api.get<TaskPageView>(`/v1/tasks/archived?scope=${scope}&limit=${ARCHIVED_PAGE_SIZE}`, { signal }),
  });
}

export function useRestoreTask() {
  return useMutation({
    mutationFn: ({ id, version }: { id: string; version: number }) =>
      api.post<TaskView>(`/v1/tasks/${id}/restore`, { expected_version: version } satisfies RestoreTaskRequest),
  });
}

/** How many tasks one list shows on the server, whatever the page loaded:
 * the number "Mark all" asks about. Read when it is asked, never cached. */
export function fetchTaskCount(status: TaskStatus, scope: TaskScope): Promise<number> {
  return api
    .get<TaskCountView>(`/v1/tasks/count?status=${status}&scope=${scope}`)
    .then((counted) => counted.count);
}

/** Completes or reopens many tasks in one call: the ones named, or a whole
 * list as the server reads it. A write of many rows goes under a key of its
 * own, like a create, so the transport's retry of it is safe. */
export function useBulkTasks() {
  return useMutation({
    mutationFn: (body: BulkTasksRequest) =>
      api.post<BulkTasksView>("/v1/tasks/bulk", body, { idempotencyKey: crypto.randomUUID() }),
  });
}
