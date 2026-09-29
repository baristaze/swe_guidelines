// The one upgrade dialog: what a refusal met, the plans, and a checkout for
// the plan that lifts it. Someone who cannot change the plan is told whom to ask.
import { useState } from "react";
import type { Plan } from "../../api";
import { Banner, Button, ErrorText, Muted } from "../../design/kit";
import { useBilling, useOpenBillingPortal, useStartCheckout } from "../../queries/billing";
import { useUpgradeStore } from "../../store/upgrade";
import { ASK_AN_OWNER, boundSentence, checkoutFailure, planName } from "./billingModel";
import { PlanList } from "./PlanList";

export function UpgradeDialog() {
  const limit = useUpgradeStore((s) => s.limit);
  const close = useUpgradeStore((s) => s.close);
  if (limit === null) return null;
  return <UpgradeDialogOpen close={close} />;
}

function UpgradeDialogOpen({ close }: { close: () => void }) {
  const limit = useUpgradeStore((s) => s.limit)!;
  const billing = useBilling();
  const checkout = useStartCheckout();
  const portal = useOpenBillingPortal();
  const [failure, setFailure] = useState<string | null>(null);
  const suggested = limit.suggested_plan;

  const start = async (plan: Plan) => {
    setFailure(null);
    try {
      await checkout.mutateAsync(plan);
    } catch (caught) {
      setFailure(checkoutFailure(caught));
    }
  };

  // An org that pays already changes plan on the processor's page.
  const manage = async () => {
    setFailure(null);
    try {
      await portal.mutateAsync();
    } catch (caught) {
      setFailure(checkoutFailure(caught));
    }
  };

  const canManage = billing.data?.can_manage ?? false;
  return (
    <div className="acme-dialog-backdrop" onClick={close}>
      <div
        className="acme-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="upgrade-title"
        onClick={(event) => event.stopPropagation()}
      >
        <h2 id="upgrade-title" className="acme-card-title">
          Your plan has reached a limit
        </h2>
        <p style={{ margin: 0 }}>{boundSentence(limit)}</p>
        {billing.data ? (
          <PlanList plans={billing.data.plans} current={billing.data.plan} suggested={suggested} />
        ) : (
          <Muted>Loading the plans</Muted>
        )}
        {billing.data && !canManage ? <Banner>{ASK_AN_OWNER}</Banner> : null}
        {failure ? <ErrorText>{failure}</ErrorText> : null}
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 12 }}>
          <Button tone="plain" onClick={close}>
            Not now
          </Button>
          {canManage && suggested && billing.data?.paid_plan === null ? (
            <Button onClick={() => void start(suggested as Plan)} disabled={checkout.isPending}>
              Upgrade to {planName(suggested)}
            </Button>
          ) : null}
          {canManage && billing.data?.paid_plan ? (
            <Button onClick={() => void manage()} disabled={portal.isPending}>
              Change plan
            </Button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
