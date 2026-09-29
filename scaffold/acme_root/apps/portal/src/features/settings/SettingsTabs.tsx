// The two parts of org settings: the members and keys, and the plan.
import { useLocation, useNavigate } from "react-router-dom";
import { SegmentedControl } from "../../design/kit";

type Part = "/settings" | "/settings/billing";

export function SettingsTabs() {
  const location = useLocation();
  const navigate = useNavigate();
  const value: Part = location.pathname.startsWith("/settings/billing") ? "/settings/billing" : "/settings";
  return (
    <SegmentedControl<Part>
      label="Settings"
      value={value}
      options={[
        { value: "/settings", label: "General" },
        { value: "/settings/billing", label: "Billing" },
      ]}
      onChange={(to) => navigate(to)}
    />
  );
}
