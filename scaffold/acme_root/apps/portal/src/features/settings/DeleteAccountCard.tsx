import { Button, Card, ErrorText, Muted, TextField } from "../../design/kit";
import { tokens } from "../../design/tokens";
import { GONE_LINE } from "./deleteAccountModel";
import type { DeleteAccountVm } from "./useDeleteAccountVm";

/** The last card of Settings: the person deletes their own account. */
export function DeleteAccountCard({ vm }: { vm: DeleteAccountVm }) {
  return (
    <Card title="Delete my account">
      <div style={{ display: "grid", gap: tokens.space.md }} data-delete-account>
        <Muted>
          Your account, your personal org with its files, and your place in every team org are deleted.{" "}
          {GONE_LINE} There is no undo. What you made in a team org stays with that team, shown as a former member&apos;s.
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
              label={`Type your email, ${vm.email ?? ""}, to confirm`}
              type="email"
              autoComplete="off"
              value={vm.typed}
              onChange={vm.setTyped}
            />
            {vm.refusal ? <ErrorText>{vm.refusal}</ErrorText> : null}
            {vm.strandedHere ? (
              <div style={{ display: "flex", flexWrap: "wrap", gap: tokens.space.md }} data-ways-out>
                <a href="#members" className="acme-link">
                  Make someone else an owner
                </a>
                <a href="#delete-organization" className="acme-link">
                  Delete this organization
                </a>
              </div>
            ) : null}
            <div style={{ display: "flex", flexWrap: "wrap", gap: tokens.space.sm }}>
              <Button type="submit" tone="danger" disabled={!vm.mayConfirm}>
                {vm.deleting ? "Deleting…" : "Delete my account"}
              </Button>
              <Button tone="plain" onClick={vm.cancel} disabled={vm.deleting}>
                Cancel
              </Button>
            </div>
          </form>
        ) : (
          <div>
            <Button tone="danger" onClick={vm.start}>
              Delete my account
            </Button>
          </div>
        )}
      </div>
    </Card>
  );
}
