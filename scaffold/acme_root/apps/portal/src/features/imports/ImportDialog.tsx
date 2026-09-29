// The import's small dialog: a CSV file picked, what its columns are, and the
// start. The rows are read in the background; the page shows how far it is.
import { Button, ErrorText, Muted } from "../../design/kit";
import { tokens } from "../../design/tokens";
import { humanSize } from "../attachments/attachmentsModel";
import type { ImportVm } from "./useImportVm";

export function ImportDialog({ vm }: { vm: ImportVm }) {
  if (!vm.dialogOpen) return null;
  return (
    <div className="acme-dialog-backdrop" onClick={vm.closeDialog}>
      <div
        className="acme-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="import-title"
        onClick={(event) => event.stopPropagation()}
      >
        <h2 id="import-title" className="acme-card-title">
          Import tasks
        </h2>
        <p style={{ margin: 0 }}>
          A CSV file, one task a row. The columns are <code>title</code>, and if you like <code>notes</code>,{" "}
          <code>due_on</code> (2026-10-01), and <code>assignee_email</code> (a member of this org).
        </p>
        <Muted style={{ display: "block", marginTop: tokens.space.sm }}>
          Up to 5,000 rows and 1 MB. The tasks go to the bottom of the open list.
        </Muted>
        <label className="acme-label" style={{ marginTop: tokens.space.md }}>
          <span>File</span>
          <input
            type="file"
            accept=".csv,text/csv"
            className="acme-field"
            onChange={(event) => vm.pick(event.target.files?.[0] ?? null)}
          />
        </label>
        {vm.picked ? (
          <Muted style={{ display: "block", marginTop: tokens.space.xs }}>
            {vm.picked.name}, {humanSize(vm.picked.size)}
          </Muted>
        ) : null}
        {vm.failure ? <ErrorText>{vm.failure}</ErrorText> : null}
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 12 }}>
          <Button tone="plain" onClick={vm.closeDialog} disabled={vm.starting}>
            Cancel
          </Button>
          <Button onClick={() => void vm.start()} disabled={!vm.picked || vm.starting}>
            {vm.starting ? "Uploading…" : "Import"}
          </Button>
        </div>
      </div>
    </div>
  );
}
