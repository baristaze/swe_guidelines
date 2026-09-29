// The current org in the chrome. Its name goes home. Its caret opens a menu:
// the other places to switch to, when there are any, and a new team org.
import { Link } from "react-router-dom";
import { Caret, Menu, MenuItem, MenuSeparator, MenuText, PlusIcon, UserIcon, UsersIcon } from "../design/kit";
import { tokens } from "../design/tokens";
import { placeNote } from "./orgChipModel";
import { useOrgChipVm } from "./useOrgChipVm";

export function OrgChip() {
  const vm = useOrgChipVm();
  const menuName = vm.canSwitch ? "Switch or create an organization" : "Create an organization";
  return (
    <div className="acme-org-chip">
      <Link to="/" className="acme-org-home" title="Home">
        <span className="acme-org-avatar" aria-hidden>
          {vm.orgName.trim().charAt(0).toUpperCase()}
        </span>
        {vm.orgName}
        {vm.personal ? (
          <span style={{ color: tokens.color.muted, fontWeight: 400, fontSize: tokens.font.size.sm }}>personal</span>
        ) : null}
      </Link>
      <Menu
        label="Organizations"
        triggerLabel={menuName}
        triggerTitle={menuName}
        triggerClassName="acme-org-caret"
        minWidth={240}
        disabled={!vm.canOpen || vm.switching}
        trigger={<Caret />}
      >
        {vm.canSwitch ? <MenuText>Switch to</MenuText> : null}
        {vm.others.map((membership) => (
          <MenuItem
            key={membership.org.id}
            onSelect={() => void vm.pick(membership)}
            icon={membership.org.kind === "personal" ? <UserIcon /> : <UsersIcon />}
          >
            <span>{membership.org.name}</span>
            <span style={{ color: tokens.color.muted, fontSize: tokens.font.size.sm }}>{placeNote(membership)}</span>
          </MenuItem>
        ))}
        {vm.canSwitch ? <MenuSeparator /> : null}
        <MenuItem onSelect={vm.newOrg} icon={<PlusIcon />}>
          <span>New organization…</span>
        </MenuItem>
      </Menu>
    </div>
  );
}
