// The account menu at the right of the bar: who is signed in and where,
// Settings, the theme, and Sign out. It opens on a click, never on hover.
import type { ReactNode } from "react";
import {
  Caret,
  LogOutIcon,
  Menu,
  MenuItem,
  MenuItemRadio,
  MenuSeparator,
  MenuText,
  MonitorIcon,
  MoonIcon,
  SettingsIcon,
  SunIcon,
} from "../design/kit";
import type { ThemePreference } from "./themeModel";
import { useAccountMenuVm } from "./useAccountMenuVm";

const THEME_ICONS: Record<ThemePreference, ReactNode> = {
  system: <MonitorIcon />,
  light: <SunIcon />,
  dark: <MoonIcon />,
};

export function AccountMenu() {
  const vm = useAccountMenuVm();
  return (
    <Menu
      label="Account"
      triggerLabel={vm.email ? `Account: ${vm.email}` : "Account"}
      align="end"
      minWidth={240}
      disabled={!vm.ready}
      trigger={
        <>
          <span className="acme-account-email">{vm.email}</span>
          <span className="acme-account-email" data-short>
            {vm.shortEmail}
          </span>
          <Caret />
        </>
      }
    >
      <MenuText strong>{vm.email}</MenuText>
      <MenuText>{vm.orgName}</MenuText>
      <MenuSeparator />
      <MenuItem onSelect={vm.openSettings} icon={<SettingsIcon />}>
        Settings
      </MenuItem>
      <MenuSeparator />
      <div role="group" aria-label="Theme">
        <MenuText>Theme</MenuText>
        {vm.themes.map((choice) => (
          <MenuItemRadio
            key={choice.value}
            checked={vm.theme === choice.value}
            onSelect={() => vm.setTheme(choice.value)}
            icon={THEME_ICONS[choice.value]}
          >
            {choice.label}
          </MenuItemRadio>
        ))}
      </div>
      <MenuSeparator />
      <MenuItem onSelect={vm.signOut} disabled={vm.signingOut} icon={<LogOutIcon />}>
        Sign out
      </MenuItem>
    </Menu>
  );
}
