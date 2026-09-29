import { Button, Card, ErrorText, Muted, TextField } from "../../design/kit";
import { tokens } from "../../design/tokens";
import { ORG_GONE_LINE } from "./deleteOrgModel";
import type { DeleteOrgVm } from "./useDeleteOrgVm";

/** An owner deletes their team org, for everyone in it. Shown to owners of a
 * team org only: a personal org goes with its person's account. */
export function DeleteOrgCard({ vm }: { vm: DeleteOrgVm }) {
  if (!vm.shown) return null;
  return (
    <Card title="Delete this organization" id="delete-organization">
      <div style={{ display: "grid", gap: tokens.space.md }} data-delete-org>
        <Muted>
          Everyone in {vm.name} loses it now: its tasks and files, its members and their keys, its plan, whose
          subscription ends at once, and its Slack app. {ORG_GONE_LINE} There is no undo. You land in your personal
          org.
        </Muted>
        {vm.open ? (
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void vm.confirm();
            }}
            style={{ display: "grid", gap: tokens.space.sm }}
          >
            <TextField
              label={`Type the organization's name, ${vm.name ?? ""}, to confirm`}
              autoComplete="off"
              value={vm.typed}
              onChange={vm.setTyped}
            />
            {vm.refusal ? <ErrorText>{vm.refusal}</ErrorText> : null}
            <div style={{ display: "flex", flexWrap: "wrap", gap: tokens.space.sm }}>
              <Button type="submit" tone="danger" disabled={!vm.mayConfirm}>
                {vm.deleting ? "Deleting…" : "Delete this organization"}
              </Button>
              <Button tone="plain" onClick={vm.cancel} disabled={vm.deleting}>
                Cancel
              </Button>
            </div>
          </form>
        ) : (
          <div>
            <Button tone="danger" onClick={vm.start}>
              Delete this organization
            </Button>
          </div>
        )}
      </div>
    </Card>
  );
}
