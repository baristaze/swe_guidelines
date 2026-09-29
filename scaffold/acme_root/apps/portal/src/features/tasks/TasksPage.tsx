import { useState, type DragEvent } from "react";
import { AppNav } from "../../app/AppNav";
import {
  ActionBar,
  Banner,
  Button,
  CheckCheckIcon,
  CloseIcon,
  ConfirmDialog,
  FoldingCard,
  IconButton,
  LinkButton,
  ListChecksIcon,
  Menu,
  MenuItem,
  MenuSeparator,
  MoreIcon,
  Muted,
  Page,
  RotateCcwIcon,
  SegmentedControl,
} from "../../design/kit";
import { tokens } from "../../design/tokens";
import { ArchivedTasks } from "./ArchivedTasks";
import { TaskItem, type RowSelection } from "./TaskItem";
import { useBulkVm, type BulkVm } from "./useBulkVm";
import type { TaskSection } from "../../store/preferences";
import type { DropSide } from "./tasksModel";
import { SCOPE_CHOICES, scopeHeading } from "./scopeModel";
import { useTasksVm, type TasksVm } from "./useTasksVm";
import { PaymentNotice } from "../billing/PaymentNotice";
import { ImportDialog } from "../imports/ImportDialog";
import { ImportStatus } from "../imports/ImportStatus";
import { useImportVm, type ImportVm } from "../imports/useImportVm";

export function TasksPage() {
  const vm = useTasksVm();
  const imports = useImportVm(vm.canWrite);
  return (
    <Page heading={<TasksHeading vm={vm} imports={imports} />} nav={<AppNav />} notice={<PaymentNotice />}>
      <ImportStatus vm={imports} />
      <ImportDialog vm={imports} />
      {vm.error ? (
        <Banner>
          {vm.error} <LinkButton onClick={vm.dismissError}>dismiss</LinkButton>
        </Banner>
      ) : null}
      {vm.canWrite ? (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void vm.add();
          }}
          style={{ display: "flex", gap: tokens.space.sm }}
        >
          <input
            aria-label="New task"
            placeholder={vm.scope === "mine" ? "Add a task for yourself…" : "Add a task for the team…"}
            value={vm.title}
            onChange={(event) => vm.setTitle(event.target.value)}
            maxLength={500}
            style={{
              flex: 1,
              font: "inherit",
              fontSize: tokens.font.size.md,
              padding: tokens.space.sm,
              border: `1px solid ${tokens.color.border}`,
              borderRadius: tokens.radius.sm,
            }}
          />
          <Button type="submit" disabled={!vm.title.trim() || vm.adding}>
            Add
          </Button>
        </form>
      ) : null}
      {vm.loading ? (
        <Muted>Loading</Muted>
      ) : (
        // Keyed by scope: switching lists mounts fresh rows without the arrival motion.
        <TaskGroups key={vm.scope} vm={vm} />
      )}
    </Page>
  );
}

/** The heading is the list on screen: a switch when the org has more than
 * one member, the one list's name when the person is alone in it. The
 * import sits at its right. */
function TasksHeading({ vm, imports }: { vm: TasksVm; imports: ImportVm }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: tokens.space.md, minHeight: 40, marginBottom: tokens.space.lg }}>
      {vm.heading === "switch" ? (
        <>
          <h1 className="acme-sr-only">{scopeHeading(vm.scope)}</h1>
          <SegmentedControl large label="Which tasks" value={vm.scope} options={SCOPE_CHOICES} onChange={vm.setScope} />
        </>
      ) : (
        <h1 className="acme-title" style={{ margin: 0 }}>
          {scopeHeading(vm.scope)}
        </h1>
      )}
      {imports.canImport ? (
        <div style={{ marginLeft: "auto" }}>
          <Button tone="plain" onClick={imports.openDialog}>
            Import
          </Button>
        </div>
      ) : null}
    </div>
  );
}

function TaskGroups({ vm }: { vm: TasksVm }) {
  const [listMountedAt] = useState(() => Date.now());
  const [grabbedId, setGrabbedId] = useState<string | null>(null);
  const [draggedId, setDraggedId] = useState<string | null>(null);
  const [over, setOver] = useState<{ id: string; side: DropSide } | null>(null);
  // The row the keyboard enters each list on; the first row until one is focused.
  const [entryRows, setEntryRows] = useState<Record<TaskSection, string | null>>({ open: null, done: null });
  const openTasks = vm.open.filter((e) => !e.leaving).map((e) => e.task);
  const doneTasks = vm.done.map((e) => e.task);
  const bulk = useBulkVm({ scope: vm.scope, open: openTasks, done: doneTasks, canWrite: vm.canWrite });

  const endDrag = () => {
    setGrabbedId(null);
    setDraggedId(null);
    setOver(null);
  };

  // A folded section keeps no selection: nothing hidden is acted on.
  const toggleFolded = (section: TaskSection) => {
    if (!vm.folded[section] && bulk.selecting(section)) bulk.clear();
    vm.toggleFolded(section);
  };

  const rowSelection = (section: TaskSection, tasks: TasksVm["open"][number]["task"][], id: string): RowSelection => {
    const ids = tasks.map((t) => t.id);
    const kept = entryRows[section];
    const entryId = kept !== null && ids.includes(kept) ? kept : ids[0];
    return {
      selected: bulk.isSelected(section, id),
      active: bulk.selecting(section),
      entry: id === entryId,
      onPick: (how) => bulk.select(section, id, how),
      onStep: (step, extend) => {
        const next = ids[ids.indexOf(id) + step];
        if (next === undefined) return;
        document.querySelector<HTMLElement>(`[data-task-row="${next}"]`)?.focus();
        if (extend) bulk.select(section, next, bulk.selecting(section) ? "range" : "toggle");
      },
      onFocus: () => setEntryRows((current) => (current[section] === id ? current : { ...current, [section]: id })),
    };
  };

  const itemProps = (entry: TasksVm["open"][number]) => ({
    task: entry.task,
    row: entry.row,
    leaving: entry.leaving,
    listMountedAt,
    canWrite: vm.canWrite,
    editing: vm.editingId === entry.task.id,
    saving: vm.saving,
    assigneeOptions: vm.assigneeOptions,
    onEdit: () => vm.startEditing(entry.task.id),
    onCancelEdit: vm.stopEditing,
    onSave: (edit: Parameters<TasksVm["save"]>[1]) => void vm.save(entry.task, edit),
    onDelete: () => void vm.destroy(entry.task),
  });

  const listProps = (section: TaskSection) =>
    vm.canWrite
      ? {
          role: "grid",
          "aria-multiselectable": true,
          "aria-label": section === "open" ? "Open tasks" : "Done tasks",
          className: bulk.selecting(section) ? "acme-selecting" : undefined,
        }
      : {};

  return (
    <>
      <FoldingCard
        title={`Open (${openTasks.length})`}
        folded={vm.folded.open}
        onToggle={() => toggleFolded("open")}
        actions={vm.canWrite ? <SectionMenu section="open" empty={openTasks.length === 0} bulk={bulk} /> : null}
      >
        {vm.open.length === 0 ? <Muted>Nothing open. Add a task above.</Muted> : null}
        <ul style={{ margin: 0, padding: 0 }} {...listProps("open")}>
          {vm.open.map((entry) => (
            <TaskItem
              key={entry.task.id}
              {...itemProps(entry)}
              select={vm.canWrite && !entry.leaving ? rowSelection("open", openTasks, entry.task.id) : undefined}
              onToggle={() => void vm.complete(entry.task)}
              drag={
                vm.canWrite && !entry.leaving
                  ? {
                      draggable: grabbedId === entry.task.id,
                      dragging: draggedId === entry.task.id,
                      dropIndicator: over?.id === entry.task.id && draggedId !== entry.task.id ? over.side : null,
                      onGrab: () => setGrabbedId(entry.task.id),
                      onDragStart: (event: DragEvent) => {
                        event.dataTransfer.effectAllowed = "move";
                        event.dataTransfer.setData("text/plain", entry.task.id);
                        setDraggedId(entry.task.id);
                      },
                      onDragOver: (event: DragEvent) => {
                        if (!draggedId) return;
                        event.preventDefault();
                        const box = event.currentTarget.getBoundingClientRect();
                        const side: DropSide = event.clientY < box.top + box.height / 2 ? "before" : "after";
                        if (over?.id !== entry.task.id || over.side !== side) setOver({ id: entry.task.id, side });
                      },
                      onDrop: (event: DragEvent) => {
                        event.preventDefault();
                        if (draggedId && over) vm.drop(draggedId, over.id, over.side);
                        endDrag();
                      },
                      onDragEnd: endDrag,
                    }
                  : undefined
              }
            />
          ))}
        </ul>
        {vm.hasMoreOpen ? (
          <div style={{ paddingTop: tokens.space.md }}>
            {vm.loadingMoreOpen ? <Muted>Loading</Muted> : <LinkButton onClick={vm.showMoreOpen}>Show more</LinkButton>}
          </div>
        ) : null}
      </FoldingCard>
      <FoldingCard
        title="Done"
        folded={vm.folded.done}
        onToggle={() => toggleFolded("done")}
        actions={vm.canWrite ? <SectionMenu section="done" empty={doneTasks.length === 0} bulk={bulk} /> : null}
      >
        {vm.done.length === 0 ? <Muted>Nothing done yet.</Muted> : null}
        <ul style={{ margin: 0, padding: 0 }} {...listProps("done")}>
          {vm.done.map((entry) => (
            <TaskItem
              key={entry.task.id}
              {...itemProps(entry)}
              select={vm.canWrite ? rowSelection("done", doneTasks, entry.task.id) : undefined}
              onToggle={() => void vm.reopen(entry.task)}
            />
          ))}
        </ul>
        {vm.hasMoreDone ? (
          <div style={{ paddingTop: tokens.space.md }}>
            {vm.loadingMoreDone ? <Muted>Loading</Muted> : <LinkButton onClick={vm.showMoreDone}>Show more</LinkButton>}
          </div>
        ) : null}
      </FoldingCard>
      {/* The archive is under the done list, and folds with it. */}
      {vm.folded.done ? null : <ArchivedTasks scope={vm.scope} canWrite={vm.canWrite} />}
      <SelectionBar bulk={bulk} />
      {bulk.asking ? (
        <ConfirmDialog
          title={bulk.asking.title}
          confirmLabel={bulk.asking.confirmLabel}
          tone="danger"
          busy={bulk.asking.waiting}
          onConfirm={bulk.confirmAll}
          onCancel={bulk.cancelAll}
        >
          {bulk.asking.body}
        </ConfirmDialog>
      ) : null}
    </>
  );
}

/** A section's ⋯ menu: select every task of the section, loaded or not, and
 * the change of all of them behind a question. */
function SectionMenu({ section, empty, bulk }: { section: TaskSection; empty: boolean; bulk: BulkVm }) {
  const name = section === "open" ? "Open" : "Done";
  return (
    <Menu
      label={`${name} tasks`}
      trigger={<MoreIcon />}
      triggerLabel={`More actions for ${name.toLowerCase()} tasks`}
      triggerTitle="More actions"
      align="end"
      minWidth={200}
    >
      <MenuItem onSelect={() => bulk.selectAll(section)} disabled={empty} icon={<ListChecksIcon />}>
        Select all
      </MenuItem>
      <MenuSeparator />
      <MenuItem
        tone="danger"
        onSelect={() => bulk.askAll(section)}
        disabled={empty}
        icon={section === "open" ? <CheckCheckIcon /> : <RotateCcwIcon />}
      >
        {section === "open" ? "Mark all as done…" : "Reopen all…"}
      </MenuItem>
    </Menu>
  );
}

/** The bar at the foot of the window while anything is selected. */
function SelectionBar({ bulk }: { bulk: BulkVm }) {
  if (bulk.action === null) return null;
  return (
    <ActionBar label="Selected tasks">
      <span className="acme-action-bar-count" aria-live="polite">
        {bulk.count === null ? "Counting…" : `${bulk.count} selected`}
      </span>
      <Button onClick={bulk.apply} disabled={bulk.busy || bulk.count === null || bulk.count === 0}>
        {bulk.action}
      </Button>
      <IconButton label="Clear the selection" onClick={bulk.clear}>
        <CloseIcon />
      </IconButton>
    </ActionBar>
  );
}
