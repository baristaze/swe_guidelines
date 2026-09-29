import { AppNav } from "../../app/AppNav";
import { Button, Card, ErrorText, Muted, Page, TextField } from "../../design/kit";
import { tokens } from "../../design/tokens";
import { useNewOrgVm } from "./useNewOrgVm";
import { PaymentNotice } from "../billing/PaymentNotice";

export function NewOrgPage() {
  const vm = useNewOrgVm();
  return (
    <Page title="New organization" nav={<AppNav />} notice={<PaymentNotice />}>
      <Card>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void vm.submit();
          }}
          style={{ display: "grid", gap: tokens.space.md, maxWidth: 420 }}
        >
          <Muted style={{ fontSize: tokens.font.size.sm }}>
            An organization for a team. You own it, and you move into it once it is made.
          </Muted>
          <TextField label="Name" value={vm.name} onChange={vm.setName} autoComplete="organization" />
          <TextField
            label="Short name (optional; in links and the command line)"
            value={vm.slug}
            onChange={vm.setSlug}
            placeholder={vm.slugPlaceholder}
          />
          {vm.error ? <ErrorText>{vm.error}</ErrorText> : null}
          <div style={{ display: "flex", gap: tokens.space.sm }}>
            <Button type="submit" disabled={vm.busy}>
              {vm.busy ? "Creating…" : "Create organization"}
            </Button>
            <Button tone="plain" onClick={vm.cancel} disabled={vm.busy}>
              Cancel
            </Button>
          </div>
        </form>
      </Card>
    </Page>
  );
}
