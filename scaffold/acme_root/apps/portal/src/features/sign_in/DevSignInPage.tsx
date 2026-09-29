import { Navigate } from "react-router-dom";
import { runtimeConfig } from "../../app/config";
import { Button, Card, ErrorText, Muted, Page, TextField } from "../../design/kit";
import { tokens } from "../../design/tokens";
import { ChooseOrgCard } from "./ChooseOrgCard";
import { useDevSignInVm } from "./useDevSignInVm";

export function DevSignInPage() {
  if (!runtimeConfig().devSignIn) return <Navigate to="/login" replace />;
  return <DevSignInForm />;
}

function DevSignInForm() {
  const vm = useDevSignInVm();
  return (
    <Page title="Local sign-in" narrow>
      {vm.choice.kind === "several" ? (
        <ChooseOrgCard memberships={vm.choice.memberships} onPick={(m) => void vm.pick(m)} busy={vm.busy} />
      ) : (
        <Card>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void vm.submit();
            }}
            style={{ display: "grid", gap: tokens.space.md }}
          >
            <Muted>
              The local stack signs anyone in by address alone, with no email sent. A deployed environment has no such
              door.
            </Muted>
            <TextField label="Email" type="email" value={vm.email} onChange={vm.setEmail} autoComplete="username" />
            <TextField label="Name (for a new person)" value={vm.name} onChange={vm.setName} />
            {vm.error ? <ErrorText>{vm.error}</ErrorText> : null}
            <Button type="submit" wide disabled={vm.busy}>
              {vm.busy ? "Signing in…" : "Sign in"}
            </Button>
          </form>
        </Card>
      )}
    </Page>
  );
}
