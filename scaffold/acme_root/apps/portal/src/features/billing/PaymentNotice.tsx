// What an owner or an admin sees when the processor could not collect the
// latest invoice: the sentence, and the one way out, the processor's page
// for a new payment method. The billing page shows it in place; every other
// signed-in page shows it above its title, until a payment goes through or
// the subscription ends.
import { useState } from "react";
import { ApiError } from "../../api";
import { Banner, Button, ErrorText } from "../../design/kit";
import { useBilling, useOpenBillingPortal } from "../../queries/billing";
import { checkoutFailure, paymentFailedSentence, UPDATE_PAYMENT_METHOD } from "./billingModel";

export function PaymentNotice() {
  const billing = useBilling();
  const portal = useOpenBillingPortal();
  const [failure, setFailure] = useState<string | null>(null);
  const sentence = billing.data ? paymentFailedSentence(billing.data) : null;
  if (sentence === null) return null;

  const update = async () => {
    setFailure(null);
    try {
      await portal.mutateAsync("payment_method_update");
    } catch (caught) {
      setFailure(caught instanceof ApiError ? checkoutFailure(caught) : "The payment page did not open.");
    }
  };

  return (
    <Banner>
      <strong>A payment failed.</strong> {sentence}
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginTop: 8 }}>
        <Button onClick={() => void update()} disabled={portal.isPending}>
          {UPDATE_PAYMENT_METHOD}
        </Button>
        {failure ? <ErrorText>{failure}</ErrorText> : null}
      </div>
    </Banner>
  );
}
