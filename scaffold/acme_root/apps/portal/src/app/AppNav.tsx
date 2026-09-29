// The bar above every signed-in page. On the left, the org chip: its name goes
// home, its caret switches org. On the right, the gear goes
// to Settings and the account menu holds who is signed in, the theme, and
// Sign out. The live channel's state is the dot in the bottom-right corner.
import { Link, useLocation } from "react-router-dom";
import { SettingsIcon } from "../design/kit";
import { AccountMenu } from "./AccountMenu";
import { ConnectionDot } from "./ConnectionDot";
import { OrgChip } from "./OrgChip";

export function AppNav() {
  const inSettings = useLocation().pathname.startsWith("/settings");
  return (
    <nav className="acme-nav" aria-label="Main">
      <OrgChip />
      <div className="acme-nav-end">
        <Link
          to="/settings"
          aria-label="Settings"
          title="Settings"
          aria-current={inSettings ? "page" : undefined}
          className="acme-icon-button"
        >
          <SettingsIcon />
        </Link>
        <AccountMenu />
      </div>
      <ConnectionDot />
    </nav>
  );
}
