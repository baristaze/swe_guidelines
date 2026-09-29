import { Button, Card, ErrorText, Muted, Page } from "../../design/kit";
import { tokens } from "../../design/tokens";
import { ChooseOrgCard } from "./ChooseOrgCard";
import { useCallbackVm } from "./useCallbackVm";

export function CallbackPage() {
  const vm = useCallbackVm();
  return (
    <Page title="Sign in to Acme" narrow>
      {vm.choice.kind === "several" ? (
        <ChooseOrgCard memberships={vm.choice.memberships} onPick={(m) => void vm.pick(m)} busy={vm.busy} />
      ) : (
        <Card>
          <div style={{ display: "grid", gap: tokens.space.md }}>
            {vm.error ? (
              <>
                <ErrorText>{vm.error}</ErrorText>
                <Button wide onClick={vm.again}>
                  Sign in again
                </Button>
              </>
            ) : (
              <Muted>Signing you in…</Muted>
            )}
          </div>
        </Card>
      )}
    </Page>
  );
}
