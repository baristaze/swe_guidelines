// The import the page follows: a line of how far it is, pushed as the worker
// moves it; when it parks on the plan, the upgrade and a Resume; when it
// ends, what it did, until dismissed.
import { Button, Card, LinkButton, Muted } from "../../design/kit";
import { tokens } from "../../design/tokens";
import type { ImportVm } from "./useImportVm";

export function ImportStatus({ vm }: { vm: ImportVm }) {
  const shown = vm.shown;
  if (!shown) return null;
  const percent = shown.total ? Math.round((shown.created / shown.total) * 100) : null;
  return (
    <Card>
      <div role="status" aria-live="polite" style={{ display: "grid", gap: tokens.space.sm }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: tokens.space.md, justifyContent: "space-between" }}>
          <span style={{ fontWeight: shown.failed ? 600 : undefined, color: shown.failed ? tokens.color.danger : undefined }}>
            {shown.headline}
          </span>
          {shown.ended ? <LinkButton onClick={vm.dismiss}>Dismiss</LinkButton> : null}
        </div>
        {shown.running && percent !== null ? (
          <div className="acme-progress" aria-hidden="true">
            <div className="acme-progress-bar" style={{ width: `${percent}%` }} />
          </div>
        ) : null}
        {shown.parkedOnThePlan ? (
          <div style={{ display: "flex", gap: tokens.space.sm, flexWrap: "wrap", alignItems: "center" }}>
            {vm.canUpgrade ? <Button onClick={vm.upgrade}>See plans</Button> : null}
            <Button tone="plain" onClick={() => void vm.resume()} disabled={vm.resuming}>
              Resume
            </Button>
            <Muted>
              {vm.canUpgrade
                ? "A higher plan resumes it by itself. Or finish some tasks, then Resume."
                : "Ask an owner or an admin to upgrade, or finish some tasks, then Resume."}
            </Muted>
          </div>
        ) : null}
        {shown.skipped.length > 0 && !shown.running ? (
          <details>
            <summary style={{ cursor: "pointer", color: tokens.color.muted }}>Skipped rows</summary>
            <ul style={{ margin: `${tokens.space.xs} 0 0`, paddingLeft: tokens.space.lg }}>
              {shown.skipped.map((line) => (
                <li key={line}>
                  <Muted>{line}</Muted>
                </li>
              ))}
            </ul>
          </details>
        ) : null}
      </div>
    </Card>
  );
}
