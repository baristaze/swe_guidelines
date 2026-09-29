// The org's plan: what it is, what it allows, what the org uses of it, and
// the changes an owner or an admin can make. A member reads it all.
import { AppNav } from "../../app/AppNav";
import { BackToTasks } from "../../app/BackToTasks";
import { Banner, Button, Card, ErrorText, Muted, Page, Pill } from "../../design/kit";
import { SettingsTabs } from "../settings/SettingsTabs";
import {
  ASK_AN_OWNER,
  endsOnSentence,
  monthlyText,
  planName,
  renewsText,
  sourceText,
  storageText,
  usageText,
} from "./billingModel";
import { PaymentNotice } from "./PaymentNotice";
import { PlanList } from "./PlanList";
import { useBillingVm } from "./useBillingVm";

export function BillingPage() {
  const vm = useBillingVm();
  const billing = vm.billing;
  return (
    <Page title="Settings" back={<BackToTasks />} nav={<AppNav />}>
      <SettingsTabs />
      {vm.error ? <Banner>{vm.error.message}</Banner> : null}
      {vm.loading || !billing || !vm.actions ? (
        <Muted>Loading</Muted>
      ) : (
        <>
          <PaymentNotice />
          <Card title="Plan">
            <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 12 }}>
              <Pill tone="accent">{planName(billing.plan)}</Pill>
              <Muted>{sourceText(billing)}</Muted>
            </div>
            <dl className="acme-facts">
              <dt>Members</dt>
              <dd>{usageText(billing.seats, billing.limits.members)}</dd>
              <dt>Active tasks</dt>
              <dd>{usageText(billing.active_tasks, billing.limits.active_tasks)}</dd>
              <dt>API keys</dt>
              <dd>{billing.limits.api_keys ? "Included" : "Not included"}</dd>
              <dt>Files</dt>
              <dd>{storageText(billing.storage_bytes, billing.limits.storage_bytes)}</dd>
              <dt>Price</dt>
              <dd>{monthlyText(billing.monthly_cents)}</dd>
            </dl>
            {renewsText(billing) ? <p>{renewsText(billing)}</p> : null}
            {endsOnSentence(billing) ? <Banner>{endsOnSentence(billing)}</Banner> : null}
            {!billing.can_manage ? <p><Muted>{ASK_AN_OWNER}</Muted></p> : null}
            {vm.failure ? <ErrorText>{vm.failure}</ErrorText> : null}
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 12 }}>
              {vm.actions.keep ? (
                <Button onClick={vm.resume} disabled={vm.busy}>
                  Keep {planName(billing.paid_plan)}
                </Button>
              ) : null}
              {vm.actions.manage ? (
                <Button tone="plain" onClick={vm.manage} disabled={vm.busy}>
                  Manage billing
                </Button>
              ) : null}
              {vm.actions.cancel && !vm.confirmingCancel ? (
                <Button tone="danger" onClick={vm.askCancel} disabled={vm.busy}>
                  Cancel plan
                </Button>
              ) : null}
            </div>
            {vm.confirmingCancel ? (
              <Banner>
                Cancel {planName(billing.paid_plan)}? The org keeps it until{" "}
                {billing.current_period_end?.slice(0, 10) ?? "the period ends"}, then moves to{" "}
                {planName(billing.comped_plan)}.{" "}
                <Button tone="danger" onClick={vm.cancel} disabled={vm.busy}>
                  Cancel plan
                </Button>{" "}
                <Button tone="plain" onClick={vm.keepCancel}>
                  Keep it
                </Button>
              </Banner>
            ) : null}
          </Card>
          <Card title="Plans">
            <PlanList
              plans={billing.plans}
              current={billing.plan}
              choose={vm.actions.choose ? vm.choose : undefined}
              choosing={vm.busy}
            />
            {billing.paid_plan && billing.can_manage ? (
              <Muted>To move to another paid plan, use Manage billing.</Muted>
            ) : null}
          </Card>
        </>
      )}
    </Page>
  );
}
