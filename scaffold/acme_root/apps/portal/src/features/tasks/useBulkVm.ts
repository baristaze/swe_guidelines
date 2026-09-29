import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import type { BulkTasksRequest, TaskScope, TaskView } from "../../api";
import { errorMessage } from "../../app/errorMessage";
import { placeTask, refreshTaskLists } from "../../queries/taskCache";
import { ON_TOP } from "../../queries/taskPlacement";
import { fetchTaskCount, useBulkTasks } from "../../queries/tasks";
import { notify, UNDO_TTL_MS } from "../../store/notices";
import type { TaskSection } from "../../store/preferences";
import { isPlanLimit, useUpgradeStore } from "../../store/upgrade";
import {
  actionLabel,
  confirmBody,
  confirmTitle,
  doneMessage,
  requestFor,
  requestForAll,
  undoRequest,
} from "./bulkModel";
import {
  isEmpty,
  isSelected,
  NOTHING,
  pick,
  prune,
  selectAll,
  selectedCount,
  type Pick,
  type Selection,
} from "./selectionModel";

interface Asking {
  section: TaskSection;
  /** The section's count on the server; null until it is read. */
  count: number | null;
}

/** The selection of the list on screen and the changes of many tasks: the
 * bar's action over what is selected, "Mark all" behind its question, and
 * the Undo each change offers. It lives with the list of one scope, so a
 * switch of scope or of org, which mounts the list afresh, lets it go. */
export function useBulkVm({
  scope,
  open,
  done,
  canWrite,
}: {
  scope: TaskScope;
  open: TaskView[];
  done: TaskView[];
  canWrite: boolean;
}) {
  const queryClient = useQueryClient();
  const bulk = useBulkTasks();
  const [picked, setPicked] = useState<Selection>(NOTHING);
  const [allCount, setAllCount] = useState<number | null>(null);
  const [asking, setAsking] = useState<Asking | null>(null);
  // One change at a time: the ref refuses the second before any render.
  const [busy, setBusy] = useState(false);
  const running = useRef(false);
  // A count read for one "Select all" or one question is dropped when
  // another has started since.
  const reading = useRef(0);

  const tasks = useMemo(() => ({ open, done }), [open, done]);
  const shown = useMemo(() => ({ open: open.map((t) => t.id), done: done.map((t) => t.id) }), [open, done]);
  // A picked row that left its section is no longer picked.
  const selection = useMemo(
    () => (picked.section === null ? picked : prune(picked, shown[picked.section])),
    [picked, shown],
  );

  const clear = () => {
    reading.current += 1;
    setPicked(NOTHING);
    setAllCount(null);
  };

  useEffect(() => {
    if (isEmpty(selection)) return;
    const onKey = (event: KeyboardEvent) => {
      // A menu or a dialog that took the Escape keeps it.
      if (event.key !== "Escape" || event.defaultPrevented) return;
      reading.current += 1;
      setPicked(NOTHING);
      setAllCount(null);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [selection]);

  const select = (section: TaskSection, id: string, how: Pick) => {
    if (!canWrite) return;
    setPicked(pick(selection, section, id, shown[section], how));
    setAllCount(null);
  };

  const selectEverything = (section: TaskSection) => {
    if (!canWrite) return;
    const ticket = ++reading.current;
    setPicked(selectAll(section));
    setAllCount(null);
    fetchTaskCount(section, scope).then(
      (count) => {
        if (reading.current === ticket) setAllCount(count);
      },
      () => undefined,
    );
  };

  // Every change goes through here: the rows it moves are placed at once,
  // the selection goes, and the lists are read again once the server has
  // answered, since the answer names ids and not rows. The toast says what
  // changed and offers the other action over exactly those tasks.
  const run = async (request: BulkTasksRequest, moving: TaskView[], undoable: boolean) => {
    if (running.current) return;
    running.current = true;
    setBusy(true);
    const now = new Date().toISOString();
    for (const task of moving) {
      const moved: TaskView =
        request.action === "complete"
          ? { ...task, status: "done", updated_at: now }
          : { ...task, status: "open", rank: ON_TOP, archived_at: null };
      placeTask(queryClient, moved, { optimistic: true });
    }
    clear();
    try {
      const answer = await bulk.mutateAsync(request);
      // A reopen past the plan's bound changed what it could; the rest is
      // the upgrade dialog's to offer, as it is for one task.
      if (answer.plan_limit) useUpgradeStore.getState().open(answer.plan_limit);
      const undo = undoable ? undoRequest(answer) : null;
      notify(doneMessage(answer), {
        tone: "done",
        ...(undo ? { ttlMs: UNDO_TTL_MS, action: { label: "Undo", run: () => void run(undo, [], false) } } : {}),
      });
    } catch (cause) {
      if (!isPlanLimit(cause)) notify(errorMessage(cause, "The tasks were not changed; the list was reloaded."));
    } finally {
      refreshTaskLists(queryClient);
      running.current = false;
      setBusy(false);
    }
  };

  const apply = () => {
    const section = selection.section;
    const request = requestFor(selection, scope);
    if (section === null || request === null) return;
    const moving = tasks[section].filter((t) => isSelected(selection, section, t.id));
    void run(request, moving, true);
  };

  const askAll = (section: TaskSection) => {
    if (!canWrite) return;
    const ticket = ++reading.current;
    setAsking({ section, count: null });
    fetchTaskCount(section, scope).then(
      (count) => {
        if (reading.current === ticket) setAsking({ section, count });
      },
      (cause: unknown) => {
        if (reading.current !== ticket) return;
        setAsking(null);
        notify(errorMessage(cause, "The tasks could not be counted; nothing was changed."));
      },
    );
  };

  const confirmAll = () => {
    if (asking === null || asking.count === null) return;
    const section = asking.section;
    setAsking(null);
    void run(requestForAll(section, scope), tasks[section], true);
  };

  const count = selectedCount(selection, allCount);
  return {
    selection,
    /** How many are selected; null while "Select all" counts. */
    count,
    isSelected: (section: TaskSection, id: string) => isSelected(selection, section, id),
    selecting: (section: TaskSection) => selection.section === section,
    select,
    selectAll: selectEverything,
    clear,
    /** The bar's one action, for the section the selection is in. */
    action: selection.section === null ? null : actionLabel(selection.section),
    apply,
    busy,
    askAll,
    /** The question "Mark all" asks, while it waits for an answer. */
    asking:
      asking === null
        ? null
        : {
            section: asking.section,
            title: asking.count === null ? "Counting the tasks…" : confirmTitle(asking.section, asking.count),
            body: confirmBody(asking.section, scope),
            confirmLabel: asking.section === "open" ? "Mark all done" : "Reopen all",
            waiting: asking.count === null || asking.count === 0,
          },
    confirmAll,
    cancelAll: () => {
      reading.current += 1;
      setAsking(null);
    },
  };
}

export type BulkVm = ReturnType<typeof useBulkVm>;
