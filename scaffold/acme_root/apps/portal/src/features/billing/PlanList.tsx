// The plans on offer, one row each, with a choice on the ones a person may take.
import type { Plan, PlanOfferView } from "../../api";
import { Button, Pill } from "../../design/kit";
import { limitLines, planName, priceText } from "./billingModel";

export function PlanList({
  plans,
  current,
  suggested,
  choose,
  choosing,
}: {
  plans: PlanOfferView[];
  current: Plan;
  suggested?: string | null;
  /** When absent, the list is read-only. */
  choose?: (plan: Plan) => void;
  choosing?: boolean;
}) {
  return (
    <div className="acme-plans">
      {plans.map((offer) => (
        <div key={offer.plan} className="acme-plan" data-suggested={offer.plan === suggested || undefined}>
          <div>
            <strong>{planName(offer.plan)}</strong>{" "}
            {offer.plan === current ? <Pill tone="accent">Current</Pill> : null}{" "}
            {offer.plan === suggested ? <Pill tone="accent">Suggested</Pill> : null}
            <div>{priceText(offer)}</div>
            <div className="acme-plan-bounds">{limitLines(offer.limits).join(" · ")}</div>
          </div>
          {choose && offer.plan !== current && offer.plan !== "free" ? (
            <Button tone={offer.plan === suggested ? "accent" : "plain"} onClick={() => choose(offer.plan)} disabled={choosing}>
              Choose
            </Button>
          ) : null}
        </div>
      ))}
    </div>
  );
}
