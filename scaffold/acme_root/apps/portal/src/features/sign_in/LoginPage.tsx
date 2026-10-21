import { Button, Card, ErrorText, Muted, Page } from "../../design/kit";
import { tokens } from "../../design/tokens";
import { GONE_LINE } from "../settings/deleteAccountModel";
import { useLoginVm } from "./useLoginVm";

/** `signedOut` is the page `/signed-out` shows: the identity provider's
 * logout ended its session and sent the browser back here. */
export function LoginPage({ signedOut = false }: { signedOut?: boolean }) {
  const vm = useLoginVm({ arrivedSignedOut: signedOut });
  return (
    <Page title="Sign in to Acme" narrow>
      <Card>
        <div style={{ display: "grid", gap: tokens.space.md }}>
          {vm.error ? (
            <>
              <ErrorText>{vm.error}</ErrorText>
              <Button wide onClick={vm.retry}>
                Try again
              </Button>
            </>
          ) : vm.wait ? (
            <>
              {vm.accountDeleted ? (
                <div data-account-deleted>
                  <strong>Your account is deleted.</strong> <Muted>{GONE_LINE}</Muted>
                </div>
              ) : vm.signedOut ? (
                <Muted>You are signed out.</Muted>
              ) : null}
              <Button wide onClick={vm.retry}>
                Sign in
              </Button>
            </>
          ) : (
            <Muted>Taking you to sign in…</Muted>
          )}
        </div>
      </Card>
    </Page>
  );
}
