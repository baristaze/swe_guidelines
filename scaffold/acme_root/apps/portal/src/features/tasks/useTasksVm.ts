import { useQueryClient, type InfiniteData } from "@tanstack/react-query";
import type { TaskPageView, TaskScope, TaskView } from "../../api";
import { useEffect, useMemo, useRef, useState } from "react";
import { keys } from "../../queries/keys";
import { placeTask, refreshTaskLists, removeTask } from "../../queries/taskCache";
import { ON_TOP, windowOf } from "../../queries/taskPlacement";
import {
  DONE_PAGE_SIZE,
  OPEN_PAGE_SIZE,
  useCreateTask,
  useDeleteTask,
  useDoneTasks,
  useMoveTask,
  useOpenTasks,
  useUpdateTask,
} from "../../queries/tasks";
import { useMe, useUsers } from "../../queries/tenancy";
import { isFolded, usePreferencesStore, type TaskSection } from "../../store/preferences";
import { errorMessage } from "../../app/errorMessage";
import { isPlanLimit } from "../../store/upgrade";
import { isStale, reorder, STALE_MESSAGE } from "./reorder";
import { scopeHeadingKind, shownScope } from "./scopeModel";
import {
  canAdd,
  canWrite,
  flattenPages,
  MOTION_MS,
  pagesWithOrder,
  taskRow,
  withLeaving,
  type DropSide,
  type Leaving,
} from "./tasksModel";

export interface TaskEdit {
  version: number;
  title: string;
  notes: string;
  assigneeId: string | null;
  /** The new due date ("YYYY-MM-DD"), null to clear it, or left out to keep it. */
  dueOn?: string | null;
}

export function useTasksVm() {
  const queryClient = useQueryClient();
  const me = useMe();
  const users = useUsers();
  // The member list the rows name people from, read whole: its length says
  // whether the person has company in this org.
  const members = users.isPending ? undefined : (users.data?.length ?? undefined);
  const scope = shownScope(usePreferencesStore((s) => s.taskScope), members);
  const setScope = usePreferencesStore((s) => s.setTaskScope);
  // Which sections are folded, kept per org: each org's list is its own.
  const orgId = me.data?.org.id ?? null;
  const foldedByOrg = usePreferencesStore((s) => s.folded);
  const setFolded = usePreferencesStore((s) => s.setFolded);
  const open = useOpenTasks(scope);
  const done = useDoneTasks(scope);
  const create = useCreateTask();
  const update = useUpdateTask();
  const move = useMoveTask();
  const remove = useDeleteTask();
  const [title, setTitle] = useState("");
  const [leaving, setLeaving] = useState<Leaving[]>([]);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // One save at a time. The ref refuses the second write before any render
  // can happen; the flag is what disables the button.
  const [saving, setSaving] = useState(false);
  const savingNow = useRef(false);
  // Pages of held rows shown past the pages loaded; see `windowOf`.
  const [extraOpen, setExtraOpen] = useState(0);
  const [extraDone, setExtraDone] = useState(0);
  const timers = useRef(new Set<ReturnType<typeof setTimeout>>());

  useEffect(() => {
    const pending = timers.current;
    return () => pending.forEach(clearTimeout);
  }, []);

  const meId = me.data?.user.id ?? null;
  const usersById = useMemo(() => new Map((users.data ?? []).map((u) => [u.id, u])), [users.data]);
  const openHeld = useMemo(() => flattenPages(open.data), [open.data]);
  const doneHeld = useMemo(() => flattenPages(done.data), [done.data]);
  const openTasks = useMemo(
    () => windowOf(openHeld, open.data?.pages.length ?? 1, OPEN_PAGE_SIZE, extraOpen),
    [openHeld, open.data, extraOpen],
  );
  const openView = useMemo(() => withLeaving(openTasks, leaving), [openTasks, leaving]);
  const doneTasks = useMemo(
    () => windowOf(doneHeld, done.data?.pages.length ?? 1, DONE_PAGE_SIZE, extraDone),
    [doneHeld, done.data, extraDone],
  );
  const leavingIds = useMemo(() => new Set(leaving.map((l) => l.task.id)), [leaving]);

  type Pages = InfiniteData<TaskPageView> | undefined;
  const editOpen = (edit: (data: Pages) => Pages) =>
    queryClient.setQueryData<InfiniteData<TaskPageView>>(keys.tasks.open(scope), edit);
  // The server is the truth, and every write answers with the task as it
  // wrote it: the answer is placed into every cached list, in every scope,
  // and no list is read again. A write the server refused reads them again.
  const place = (task: TaskView) => placeTask(queryClient, task);
  const refresh = () => refreshTaskLists(queryClient);
  // Every write names the version of the task as held here; a write the
  // server refused because the task changed since is said as such. Every
  // write is awaited, and none hands its callbacks to `mutate`: one hook
  // holds one observer, the writes over a task share it, and a second call
  // on an observer drops the first call's callbacks, so a refusal would go
  // unsaid and the row the server wrote unread.
  const fail = (cause: unknown) => {
    // A plan's bound is answered by the upgrade dialog, not by a line here.
    if (isPlanLimit(cause)) setError(null);
    else setError(isStale(cause) ? STALE_MESSAGE : errorMessage(cause, "Something went wrong; the list was reloaded."));
    refresh();
  };
  const later = (run: () => void) => {
    const timer = setTimeout(() => {
      timers.current.delete(timer);
      run();
    }, MOTION_MS);
    timers.current.add(timer);
  };

  const add = async () => {
    if (!canAdd(title)) return;
    // Quick creation is the title alone; a due date is set by editing the task.
    const body = { title: title.trim(), notes: "" };
    setTitle("");
    try {
      place(await create.mutateAsync(body));
      setError(null);
    } catch (cause) {
      setTitle(body.title);
      fail(cause);
    }
  };

  // Struck through at once; fades out of Open in place while it fades in at the top of Done.
  const complete = async (task: TaskView) => {
    const doneTask: TaskView = { ...task, status: "done", updated_at: new Date().toISOString() };
    const index = openView.findIndex((t) => t.id === task.id);
    setLeaving((current) => [...current.filter((l) => l.task.id !== task.id), { task: doneTask, index }]);
    placeTask(queryClient, doneTask, { optimistic: true });
    later(() => setLeaving((current) => current.filter((l) => l.task.id !== task.id)));
    try {
      // The row the server wrote replaces the optimistic one: it carries
      // the version the write bumped, and without it un-ticking or editing
      // the task is refused as someone else's change (`reopen` and `add`
      // do the same).
      place(
        await update.mutateAsync({
          id: task.id,
          body: { status: "done" },
          version: task.version,
        }),
      );
    } catch (cause) {
      fail(cause);
    }
  };

  const reopen = async (task: TaskView) => {
    // The server puts a reopened task on top of the open list.
    placeTask(
      queryClient,
      { ...task, status: "open", rank: ON_TOP },
      { optimistic: true },
    );
    try {
      place(
        await update.mutateAsync({
          id: task.id,
          body: { status: "open" },
          version: task.version,
        }),
      );
    } catch (cause) {
      fail(cause);
    }
  };

  // The form stays open until the server answers, so without the guard a
  // second submit of the same draft (Enter in the title field, then a click
  // on Save, or a double click) sends a second write naming the version the
  // first is already bumping, and the answer is a refusal that reads as
  // someone else's change when nobody else touched the task.
  const save = async (task: TaskView, edit: TaskEdit) => {
    if (!canAdd(edit.title) || savingNow.current) return;
    savingNow.current = true;
    setSaving(true);
    try {
      const saved = await update.mutateAsync({
        id: task.id,
        body: {
          title: edit.title.trim(),
          notes: edit.notes,
          assignee_id: edit.assigneeId,
          // Absent keeps the due date; null clears it; a date reschedules.
          ...(edit.dueOn !== undefined ? { due_on: edit.dueOn } : {}),
        },
        version: edit.version,
      });
      place(saved);
      setEditingId(null);
      setError(null);
    } catch (cause) {
      fail(cause);
      if (isStale(cause)) {
        setError("This task changed while you were editing. Copy your draft before closing and reopening the editor to load the latest task.");
      }
    } finally {
      savingNow.current = false;
      setSaving(false);
    }
  };

  const destroy = async (task: TaskView) => {
    removeTask(queryClient, task.id, { optimistic: true });
    setEditingId(null);
    try {
      // The answer is the task as deleted, which no list shows; placing it
      // keeps its version, so an older read never brings it back.
      place(await remove.mutateAsync({ id: task.id, version: task.version }));
    } catch (cause) {
      fail(cause);
    }
  };

  const drop = (movedId: string, targetId: string, side: DropSide) =>
    void reorder(
      openTasks,
      movedId,
      targetId,
      side,
      {
        move: (id, afterId, version) => move.mutateAsync({ id, afterId, version }),
        showOrder: (order) => editOpen((data) => pagesWithOrder(data, order)),
        // The row keeps the place the drop gave it: the server put it right
        // after the same task, and wrote no other row.
        showMoved: (task) => placeTask(queryClient, task, { inPlace: true }),
        refetch: refresh,
        report: setError,
      },
      (cause) => errorMessage(cause, "The task was not moved; the list was reloaded."),
    );

  const changeScope = (next: TaskScope) => {
    setScope(next);
    setEditingId(null);
    setExtraOpen(0);
    setExtraDone(0);
  };

  // Only the open copy of a completed task leaves; the done copy is arriving.
  const rowOf = (task: TaskView, group: "open" | "done") => ({
    task,
    row: taskRow(task, usersById, meId),
    leaving: group === "open" && leavingIds.has(task.id),
  });

  return {
    scope,
    heading: scopeHeadingKind(members),
    setScope: changeScope,
    title,
    setTitle,
    add,
    adding: create.isPending,
    saving,
    canWrite: canWrite(me.data),
    loading: open.isPending || done.isPending,
    error: error ?? open.error?.message ?? done.error?.message ?? null,
    dismissError: () => setError(null),
    open: openView.map((task) => rowOf(task, "open")),
    done: doneTasks.map((task) => rowOf(task, "done")),
    hasMoreOpen: open.hasNextPage || openHeld.length > openTasks.length,
    loadingMoreOpen: open.isFetchingNextPage,
    showMoreOpen: () =>
      openHeld.length > openTasks.length ? setExtraOpen((n) => n + 1) : void open.fetchNextPage(),
    hasMoreDone: done.hasNextPage || doneHeld.length > doneTasks.length,
    loadingMoreDone: done.isFetchingNextPage,
    showMoreDone: () =>
      doneHeld.length > doneTasks.length ? setExtraDone((n) => n + 1) : void done.fetchNextPage(),
    folded: { open: isFolded(foldedByOrg, orgId, "open"), done: isFolded(foldedByOrg, orgId, "done") },
    toggleFolded: (section: TaskSection) => {
      if (orgId !== null) setFolded(orgId, section, !isFolded(foldedByOrg, orgId, section));
    },
    editingId,
    startEditing: setEditingId,
    stopEditing: () => setEditingId(null),
    assigneeOptions: [
      { value: "", label: "Unassigned" },
      ...(users.data ?? []).map((u) => ({ value: u.id, label: u.id === meId ? `${u.display_name} (you)` : u.display_name })),
    ],
    complete,
    reopen,
    save,
    destroy,
    drop,
  };
}

export type TasksVm = ReturnType<typeof useTasksVm>;
