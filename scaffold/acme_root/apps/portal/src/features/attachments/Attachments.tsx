import { useRef, useState, type DragEvent } from "react";
import { LinkButton, Muted, Pill } from "../../design/kit";
import { tokens } from "../../design/tokens";
import { FilePreview } from "./FilePreview";
import { useAttachmentsVm } from "./useAttachmentsVm";

/** A task's files, inside the task's open view: drop files here or pick them,
 * see each one's name, size, and type, and a preview of an image, a video, a
 * sound, or a PDF; download it as well, remove it. */
export function Attachments({ taskId, canWrite }: { taskId: string; canWrite: boolean }) {
  const vm = useAttachmentsVm(taskId);
  const picker = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);

  const onDrop = (event: DragEvent) => {
    event.preventDefault();
    setOver(false);
    if (canWrite && event.dataTransfer.files.length) void vm.upload(Array.from(event.dataTransfer.files));
  };

  return (
    <div
      aria-label="Attachments"
      onDragOver={(event) => {
        if (!canWrite || !event.dataTransfer.types.includes("Files")) return;
        event.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={onDrop}
      style={{
        display: "grid",
        gap: tokens.space.sm,
        padding: tokens.space.md,
        border: `1px dashed ${over ? tokens.color.accent : tokens.color.border}`,
        borderRadius: tokens.radius.sm,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: tokens.space.sm }}>
        <strong style={{ fontSize: tokens.font.size.sm }}>Attachments</strong>
        {canWrite ? (
          <>
            <Muted style={{ fontSize: tokens.font.size.sm }}>drop files here or</Muted>
            <LinkButton onClick={() => picker.current?.click()}>choose files</LinkButton>
            <input
              ref={picker}
              type="file"
              multiple
              aria-label="Choose files to attach"
              style={{ display: "none" }}
              onChange={(event) => {
                const chosen = Array.from(event.target.files ?? []);
                event.target.value = "";
                if (chosen.length) void vm.upload(chosen);
              }}
            />
          </>
        ) : null}
      </div>
      {vm.loading ? <Muted>Loading</Muted> : null}
      {!vm.loading && vm.rows.length === 0 && vm.uploading.length === 0 ? <Muted>No files yet.</Muted> : null}
      <ul style={{ margin: 0, padding: 0, display: "grid", gap: tokens.space.xs }}>
        {vm.rows.map((row) => (
          <li key={row.id} style={{ listStyle: "none", display: "grid", gap: tokens.space.xs, minWidth: 0 }}>
            <div style={{ display: "flex", alignItems: "center", gap: tokens.space.sm, minWidth: 0 }}>
            <span
              title={row.name}
              style={{ flex: "1 1 auto", minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
            >
              {row.name}
            </span>
            <Muted style={{ flexShrink: 0, fontSize: tokens.font.size.sm }}>{row.size}</Muted>
            <Pill title={row.contentType}>{row.kind}</Pill>
            <LinkButton onClick={() => void vm.download(row.id)}>{vm.busyId === row.id ? "…" : "download"}</LinkButton>
            {canWrite ? <LinkButton onClick={() => void vm.destroy(row.id)}>remove</LinkButton> : null}
            </div>
            {row.preview ? <FilePreview fileId={row.id} kind={row.preview} name={row.name} /> : null}
          </li>
        ))}
        {vm.uploading.map((u) => (
          <li key={u.key} style={{ listStyle: "none", display: "flex", gap: tokens.space.sm }}>
            <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{u.name}</span>
            <Muted style={{ fontSize: tokens.font.size.sm }}>uploading {u.size}</Muted>
          </li>
        ))}
      </ul>
      {vm.hasMore ? <Muted style={{ fontSize: tokens.font.size.sm }}>More files than one page shows.</Muted> : null}
    </div>
  );
}
