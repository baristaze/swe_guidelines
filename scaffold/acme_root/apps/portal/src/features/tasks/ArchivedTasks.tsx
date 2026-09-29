// The archive: the done tasks the daily cleanup put away after ninety days
// untouched. Closed until the person opens it; each task can be restored to
// the top of the done list.
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { TaskScope, TaskView } from "../../api";
import { errorMessage } from "../../app/errorMessage";
import { Button, Card, LinkButton, Muted } from "../../design/kit";
import { tokens } from "../../design/tokens";
import { placeTask } from "../../queries/taskCache";
import { useArchivedTasks, useRestoreTask } from "../../queries/tasks";
import { notify } from "../../store/notices";

export function ArchivedTasks({ scope, canWrite }: { scope: TaskScope; canWrite: boolean }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const archived = useArchivedTasks(scope, open);
  const restore = useRestoreTask();

  // The answer leaves the archive and takes its place in the done list; a
  // refusal reads the archive again.
  const restoreOne = async (task: TaskView) => {
    try {
      placeTask(queryClient, await restore.mutateAsync({ id: task.id, version: task.version }));
    } catch (caught) {
      notify(errorMessage(caught, `Could not restore ${task.title}.`));
      void archived.refetch();
    }
  };

  if (!open) {
    return (
      <div>
        <LinkButton onClick={() => setOpen(true)}>Show archived</LinkButton>
      </div>
    );
  }
  const items = archived.data?.items ?? [];
  return (
    <Card title="Archived">
      <Muted style={{ display: "block", marginBottom: tokens.space.sm }}>
        Done tasks nobody touched for 90 days. Nothing was deleted.
      </Muted>
      {archived.isLoading ? <Muted>Loading</Muted> : null}
      {!archived.isLoading && items.length === 0 ? <Muted>Nothing archived.</Muted> : null}
      <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "grid", gap: tokens.space.xs }}>
        {items.map((task) => (
          <li key={task.id} style={{ display: "flex", alignItems: "center", gap: tokens.space.sm }}>
            <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {task.title}
            </span>
            {canWrite ? (
              <Button tone="plain" onClick={() => void restoreOne(task)} disabled={restore.isPending}>
                Restore
              </Button>
            ) : null}
          </li>
        ))}
      </ul>
      <div style={{ paddingTop: tokens.space.md }}>
        <LinkButton onClick={() => setOpen(false)}>Hide archived</LinkButton>
      </div>
    </Card>
  );
}
