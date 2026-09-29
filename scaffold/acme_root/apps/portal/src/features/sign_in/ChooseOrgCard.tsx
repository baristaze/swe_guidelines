import type { MembershipChoiceView } from "@acme/client";
import { placeNote } from "../../app/orgChipModel";
import { Card, Muted } from "../../design/kit";

/** The person's places, the personal one first: one button each. While a
 * choice is being taken up, `busy`, none of them can be pressed again. */
export function ChooseOrgCard({
  memberships,
  onPick,
  busy = false,
}: {
  memberships: MembershipChoiceView[];
  onPick: (membership: MembershipChoiceView) => void;
  busy?: boolean;
}) {
  return (
    <Card title="Choose an organization">
      <div className="acme-menu" role="menu" aria-busy={busy}>
        {memberships.map((membership) => (
          <button
            key={membership.org.id}
            type="button"
            role="menuitem"
            className="acme-menu-item"
            disabled={busy}
            onClick={() => onPick(membership)}
          >
            <span>{membership.org.name}</span>
            <Muted>{placeNote(membership)}</Muted>
          </button>
        ))}
      </div>
    </Card>
  );
}
