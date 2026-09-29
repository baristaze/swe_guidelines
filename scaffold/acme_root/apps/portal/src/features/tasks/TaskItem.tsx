import type { TaskView } from "../../api";
import { useRef, useState, type DragEvent, type KeyboardEvent, type MouseEvent, type PointerEvent } from "react";
import { Button, LinkButton, Pill, Select, TextArea, TextField } from "../../design/kit";
import { tokens } from "../../design/tokens";
import { Attachments } from "../attachments/Attachments";
import { dueOnChange, toInputValue } from "./dueModel";
import type { Pick } from "./selectionModel";
import type { DropSide, TaskRow } from "./tasksModel";
import type { TaskEdit } from "./useTasksVm";

/** The handle's column; every row keeps it, so the boxes line up in both groups. */
const HANDLE_WIDTH = 14;

export interface DragProps {
  draggable: boolean;
  dragging: boolean;
  dropIndicator: DropSide | null;
  onGrab: () => void;
  onDragStart: (event: DragEvent) => void;
  onDragOver: (event: DragEvent) => void;
  onDrop: (event: DragEvent) => void;
  onDragEnd: () => void;
}

/** How long a finger rests on a row before it picks the row. */
export const LONG_PRESS_MS = 500;

/** How far a finger may drift and still be a press, not a scroll. */
const PRESS_SLOP_PX = 10;

/** A row as a list one selects in: whether it is picked, and how it asks to
 * be. The row's own box keeps meaning done or open; a pick is a highlight
 * (`aria-selected`), never a second box. */
export interface RowSelection {
  selected: boolean;
  /** A selection is open in this section: a plain click or tap picks too. */
  active: boolean;
  /** The row the keyboard enters the list on (the one tab stop). */
  entry: boolean;
  onPick: (how: Pick) => void;
  /** The arrows: the keyboard goes to the next or the previous row, and with
   * Shift the pick reaches it. */
  onStep: (step: 1 | -1, extend: boolean) => void;
  onFocus: () => void;
}

/** Whether a click landed on one of the row's own controls, which keep
 * their meaning (the box, the links, a field of the edit form). */
function onControl(target: EventTarget | null, row: Element): boolean {
  let element = target instanceof Element ? target : null;
  while (element && element !== row) {
    if (element.matches("button, a, input, select, textarea, label, form, [role='button']")) return true;
    element = element.parentElement;
  }
  return false;
}

export function TaskItem({
  task,
  row,
  leaving,
  listMountedAt,
  canWrite,
  editing,
  saving,
  assigneeOptions,
  drag,
  select,
  onToggle,
  onEdit,
  onCancelEdit,
  onSave,
  onDelete,
}: {
  task: TaskView;
  row: TaskRow;
  leaving: boolean;
  listMountedAt: number;
  canWrite: boolean;
  editing: boolean;
  saving: boolean;
  assigneeOptions: { value: string; label: string }[];
  drag?: DragProps;
  select?: RowSelection;
  onToggle: () => void;
  onEdit: () => void;
  onCancelEdit: () => void;
  onSave: (edit: TaskEdit) => void;
  onDelete: () => void;
}) {
  // A row that mounts after its list did is a task that just arrived: created
  // here, pushed from another tab, moved from the other group, or a new page.
  const [arriving] = useState(() => Date.now() - listMountedAt > 400);
  const [showingFiles, setShowingFiles] = useState(false);
  const struck = row.done || leaving;
  const indicator = drag?.dropIndicator;
  const press = useRef<{ timer: ReturnType<typeof setTimeout> | null; x: number; y: number; fired: boolean }>({
    timer: null,
    x: 0,
    y: 0,
    fired: false,
  });
  const endPress = () => {
    if (press.current.timer !== null) clearTimeout(press.current.timer);
    press.current.timer = null;
  };
  // ⌘ or Ctrl adds or drops the row, Shift reaches from the anchor, and while
  // a selection is open a plain click picks too. A click on the row's own
  // controls keeps its meaning.
  const onRowClick = (event: MouseEvent<HTMLLIElement>) => {
    if (!select) return;
    if (press.current.fired) {
      press.current.fired = false;
      return;
    }
    if (onControl(event.target, event.currentTarget)) return;
    if (event.metaKey || event.ctrlKey) select.onPick("toggle");
    else if (event.shiftKey) select.onPick("range");
    else if (select.active) select.onPick("toggle");
  };
  // On touch, a finger that rests on the row picks it; one that moves is a scroll.
  const onPointerDown = (event: PointerEvent<HTMLLIElement>) => {
    if (!select || event.pointerType !== "touch" || onControl(event.target, event.currentTarget)) return;
    endPress();
    press.current = {
      x: event.clientX,
      y: event.clientY,
      fired: false,
      timer: setTimeout(() => {
        press.current.timer = null;
        press.current.fired = true;
        select.onPick("toggle");
      }, LONG_PRESS_MS),
    };
  };
  const onPointerMove = (event: PointerEvent<HTMLLIElement>) => {
    if (press.current.timer === null) return;
    if (Math.hypot(event.clientX - press.current.x, event.clientY - press.current.y) > PRESS_SLOP_PX) endPress();
  };
  // Keys on the row itself: Space picks it (Shift reaches), the arrows move.
  const onRowKey = (event: KeyboardEvent<HTMLLIElement>) => {
    if (!select || event.target !== event.currentTarget) return;
    if (event.key === " ") {
      event.preventDefault();
      select.onPick(event.shiftKey ? "range" : "toggle");
    } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      select.onStep(event.key === "ArrowDown" ? 1 : -1, event.shiftKey);
    }
  };
  return (
    <li
      data-task-row={select ? task.id : undefined}
      role={select ? "row" : undefined}
      aria-selected={select ? select.selected : undefined}
      aria-label={select ? row.title : undefined}
      tabIndex={select ? (select.entry ? 0 : -1) : undefined}
      onClick={select ? onRowClick : undefined}
      onMouseDown={select ? (event) => event.shiftKey && !onControl(event.target, event.currentTarget) && event.preventDefault() : undefined}
      onPointerDown={select ? onPointerDown : undefined}
      onPointerMove={select ? onPointerMove : undefined}
      onPointerUp={select ? endPress : undefined}
      onPointerCancel={select ? endPress : undefined}
      onContextMenu={select ? (event) => press.current.fired && event.preventDefault() : undefined}
      onKeyDown={select ? onRowKey : undefined}
      onFocus={select ? (event) => event.target === event.currentTarget && select.onFocus() : undefined}
      className={[
        leaving ? "acme-leaving" : arriving ? "acme-arriving" : "",
        drag?.dragging ? "acme-dragging" : "",
        select ? "acme-task-row" : "",
      ]
        .filter(Boolean)
        .join(" ") || undefined}
      draggable={drag?.draggable ?? false}
      onDragStart={drag?.onDragStart}
      onDragOver={drag?.onDragOver}
      onDrop={drag?.onDrop}
      onDragEnd={drag?.onDragEnd}
      style={{
        listStyle: "none",
        // A row one selects in keeps a little room at its sides for the tint.
        padding: `${tokens.space.sm} ${select ? tokens.space.xs : "0"}`,
        borderTop: `2px solid ${indicator === "before" ? tokens.color.accent : "transparent"}`,
        borderBottom:
          indicator === "after" ? `2px solid ${tokens.color.accent}` : `1px solid ${tokens.color.border}`,
        pointerEvents: leaving ? "none" : undefined,
      }}
    >
      <div role={select ? "gridcell" : undefined}>
      {/* One line at any width: the title gives way first and ends in an
          ellipsis; the handle, the box, the pills, and the link keep their size. */}
      <div style={{ display: "flex", alignItems: "center", gap: tokens.space.sm }}>
        <span
          role={drag ? "button" : undefined}
          aria-label={drag ? `Drag to reorder ${row.title}` : undefined}
          aria-hidden={drag ? undefined : true}
          title={drag ? "Drag to reorder" : undefined}
          onMouseDown={drag?.onGrab}
          className={drag ? "acme-handle" : undefined}
          style={{
            width: HANDLE_WIDTH,
            flexShrink: 0,
            textAlign: "center",
            cursor: drag ? "grab" : undefined,
            color: tokens.color.muted,
            userSelect: "none",
          }}
        >
          {drag ? "⠿" : null}
        </span>
        <input
          type="checkbox"
          aria-label={row.done ? `Reopen ${row.title}` : `Mark ${row.title} done`}
          checked={struck}
          disabled={!canWrite || leaving}
          onChange={onToggle}
          style={{ width: 18, height: 18, margin: 0, flexShrink: 0, cursor: canWrite ? "pointer" : "default" }}
        />
        <span
          className="acme-task-title"
          title={row.notes ? `${row.title}\n\n${row.notes}` : row.title}
          onDoubleClick={canWrite && !leaving && !editing && !select?.active ? onEdit : undefined}
          style={{
            flex: "1 1 auto",
            minWidth: 0,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            textDecoration: struck ? "line-through" : "none",
            color: struck ? tokens.color.muted : tokens.color.text,
          }}
        >
          {row.title}
          {row.notes ? <span style={{ color: tokens.color.muted }}> — {row.notes}</span> : null}
        </span>
        <Pill title={`Created by ${row.createdBy}`}>{row.createdBy}</Pill>
        {row.assignee ? (
          <Pill title={`Assigned to ${row.assignee}`} tone={row.assignee === "you" ? "accent" : "plain"}>
            for {row.assignee}
          </Pill>
        ) : null}
        {row.due ? (
          <Pill title={row.due.title} tone={row.done || row.due.state === "upcoming" ? "plain" : "danger"}>
            {row.due.text}
          </Pill>
        ) : null}
        {!canWrite ? (
          <LinkButton onClick={() => setShowingFiles((open) => !open)}>{showingFiles ? "close" : "files"}</LinkButton>
        ) : null}
        {canWrite && !leaving ? (
          <LinkButton onClick={editing ? onCancelEdit : onEdit}>{editing ? "close" : "edit"}</LinkButton>
        ) : null}
      </div>
      {/* Someone who cannot write reads the files, previews included, without the edit form. */}
      {!canWrite && showingFiles ? (
        <div style={{ padding: `${tokens.space.md} 0 ${tokens.space.sm} ${HANDLE_WIDTH + 18 + 16}px` }}>
          <Attachments taskId={task.id} canWrite={false} />
        </div>
      ) : null}
      {editing ? <EditForm key={task.id} taskId={task.id} version={task.version} dueOn={task.due_on ?? null} row={row} saving={saving} assigneeOptions={assigneeOptions} onSave={onSave} onCancel={onCancelEdit} onDelete={onDelete} /> : null}
      </div>
    </li>
  );
}

function EditForm({
  taskId,
  version,
  dueOn,
  row,
  saving,
  assigneeOptions,
  onSave,
  onCancel,
  onDelete,
}: {
  taskId: string;
  version: number;
  dueOn: string | null;
  row: TaskRow;
  saving: boolean;
  assigneeOptions: { value: string; label: string }[];
  onSave: (edit: TaskEdit) => void;
  onCancel: () => void;
  onDelete: () => void;
}) {
  // A draft belongs to the version first opened, even when realtime refreshes the row.
  const [draftVersion] = useState(version);
  const [title, setTitle] = useState(row.title);
  const [notes, setNotes] = useState(row.notes);
  const [assigneeId, setAssigneeId] = useState(row.assigneeId ?? "");
  // The due date as first opened: the edit sends it only when it changed.
  const [dueAtOpen] = useState(() => toInputValue(dueOn));
  const [due, setDue] = useState(dueAtOpen);
  return (
    <form
      className="acme-arriving"
      onSubmit={(event) => {
        event.preventDefault();
        const change = dueOnChange(dueAtOpen, due);
        onSave({
          title,
          notes,
          assigneeId: assigneeId || null,
          version: draftVersion,
          ...(change !== undefined ? { dueOn: change } : {}),
        });
      }}
      style={{ display: "grid", gap: tokens.space.md, padding: `${tokens.space.md} 0 ${tokens.space.sm} ${HANDLE_WIDTH + 18 + 16}px` }}
    >
      <TextField label="Title" value={title} onChange={setTitle} />
      <TextArea label="Notes" value={notes} onChange={setNotes} />
      <Select label="Assigned to" value={assigneeId} options={assigneeOptions} onChange={setAssigneeId} />
      <div style={{ display: "flex", gap: tokens.space.sm, alignItems: "end" }}>
        <TextField label="Due date" type="date" value={due} onChange={setDue} />
        {due ? <LinkButton onClick={() => setDue("")}>clear due date</LinkButton> : null}
      </div>
      <Attachments taskId={taskId} canWrite />
      <div style={{ display: "flex", gap: tokens.space.sm }}>
        {/* The draft names the version it was opened at, so a second submit
            of the same draft would be refused as someone else's change. */}
        <Button type="submit" disabled={!title.trim() || saving}>
          Save
        </Button>
        <Button tone="plain" onClick={onCancel}>
          Cancel
        </Button>
        <span style={{ marginLeft: "auto" }}>
          <Button tone="danger" onClick={onDelete}>
            Delete
          </Button>
        </span>
      </div>
    </form>
  );
}
