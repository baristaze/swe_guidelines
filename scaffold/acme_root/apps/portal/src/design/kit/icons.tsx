// The kit's icons. An icon is drawn in the text's color and hidden from
// assistive technology; the words beside it are what a screen reader says.
//
// Every drawing but the gear is Lucide's (https://lucide.dev, lucide-static
// 1.48.0), copied as paths under its ISC license:
//   Copyright (c) 2026 Lucide Icons and Contributors. Permission to use,
//   copy, modify, and/or distribute this software for any purpose with or
//   without fee is hereby granted, provided that the above copyright notice
//   and this permission notice appear in all copies. THE SOFTWARE IS
//   PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES.
// Monitor, moon, log-out, and plus come to Lucide from Feather, under MIT:
//   Copyright (c) 2013-present Cole Bemis. Permission is hereby granted,
//   free of charge, to deal in the Software without restriction, provided
//   the copyright notice and this permission notice are included in all
//   copies. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND.
import type { ReactNode } from "react";

/** Lucide's 24-unit grid drawn at 16 pixels, so its 2-unit stroke is the
 * 1.3-pixel line of the kit's own marks. */
function LucideIcon({ children }: { children: ReactNode }) {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      style={{ flexShrink: 0 }}
    >
      {children}
    </svg>
  );
}

/** A gear: Settings. The product's own drawing, the one in the bar. */
export function SettingsIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" focusable="false" style={{ flexShrink: 0 }}>
      <path
        d="M6.6 1.75h2.8l.4 1.9 1.2.7 1.85-.6 1.4 2.4-1.45 1.3v1.1l1.45 1.3-1.4 2.4-1.85-.6-1.2.7-.4 1.9H6.6l-.4-1.9-1.2-.7-1.85.6-1.4-2.4L3.2 8.55v-1.1L1.75 6.15l1.4-2.4L5 4.35l1.2-.7z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinejoin="round"
      />
      <circle cx="8" cy="8" r="2" fill="none" stroke="currentColor" strokeWidth="1.3" />
    </svg>
  );
}

/** A screen: follow the system. */
export function MonitorIcon() {
  return (
    <LucideIcon>
      <rect width="20" height="14" x="2" y="3" rx="2" />
      <line x1="8" x2="16" y1="21" y2="21" />
      <line x1="12" x2="12" y1="17" y2="21" />
    </LucideIcon>
  );
}

/** The sun: light. */
export function SunIcon() {
  return (
    <LucideIcon>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2" />
      <path d="M12 20v2" />
      <path d="m4.93 4.93 1.41 1.41" />
      <path d="m17.66 17.66 1.41 1.41" />
      <path d="M2 12h2" />
      <path d="M20 12h2" />
      <path d="m6.34 17.66-1.41 1.41" />
      <path d="m19.07 4.93-1.41 1.41" />
    </LucideIcon>
  );
}

/** The moon: dark. */
export function MoonIcon() {
  return (
    <LucideIcon>
      <path d="M20.985 12.486a9 9 0 1 1-9.473-9.472c.405-.022.617.46.402.803a6 6 0 0 0 8.268 8.268c.344-.215.825-.004.803.401" />
    </LucideIcon>
  );
}

/** A door and an arrow out: sign out. */
export function LogOutIcon() {
  return (
    <LucideIcon>
      <path d="m16 17 5-5-5-5" />
      <path d="M21 12H9" />
      <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
    </LucideIcon>
  );
}

/** One person: a personal org, a member. */
export function UserIcon() {
  return (
    <LucideIcon>
      <path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2" />
      <circle cx="12" cy="7" r="4" />
    </LucideIcon>
  );
}

/** Two people: a team org. */
export function UsersIcon() {
  return (
    <LucideIcon>
      <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />
      <path d="M16 3.128a4 4 0 0 1 0 7.744" />
      <path d="M22 21v-2a4 4 0 0 0-3-3.87" />
      <circle cx="9" cy="7" r="4" />
    </LucideIcon>
  );
}

/** A plus: make a new one. */
export function PlusIcon() {
  return (
    <LucideIcon>
      <path d="M5 12h14" />
      <path d="M12 5v14" />
    </LucideIcon>
  );
}

/** A list with checks: select every row. */
export function ListChecksIcon() {
  return (
    <LucideIcon>
      <path d="M13 5h8" />
      <path d="M13 12h8" />
      <path d="M13 19h8" />
      <path d="m3 17 2 2 4-4" />
      <path d="m3 7 2 2 4-4" />
    </LucideIcon>
  );
}

/** Two checks: mark many done. */
export function CheckCheckIcon() {
  return (
    <LucideIcon>
      <path d="M18 6 7 17l-5-5" />
      <path d="m22 10-7.5 7.5L13 16" />
    </LucideIcon>
  );
}

/** An arrow turning back: reopen. */
export function RotateCcwIcon() {
  return (
    <LucideIcon>
      <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
      <path d="M3 3v5h5" />
    </LucideIcon>
  );
}

/** A crown: the owner. */
export function CrownIcon() {
  return (
    <LucideIcon>
      <path d="M11.562 3.266a.5.5 0 0 1 .876 0L15.39 8.87a1 1 0 0 0 1.516.294L21.183 5.5a.5.5 0 0 1 .798.519l-2.834 10.246a1 1 0 0 1-.956.734H5.81a1 1 0 0 1-.957-.734L2.02 6.02a.5.5 0 0 1 .798-.519l4.276 3.664a1 1 0 0 0 1.516-.294z" />
      <path d="M5 21h14" />
    </LucideIcon>
  );
}

/** A shield: an admin. */
export function ShieldIcon() {
  return (
    <LucideIcon>
      <path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z" />
    </LucideIcon>
  );
}

/** An eye: a viewer. */
export function EyeIcon() {
  return (
    <LucideIcon>
      <path d="M2.062 12.348a1 1 0 0 1 0-.696 10.75 10.75 0 0 1 19.876 0 1 1 0 0 1 0 .696 10.75 10.75 0 0 1-19.876 0" />
      <circle cx="12" cy="12" r="3" />
    </LucideIcon>
  );
}

/** A robot: a service account. */
export function BotIcon() {
  return (
    <LucideIcon>
      <path d="M12 8V4H8" />
      <rect width="16" height="12" x="4" y="8" rx="2" />
      <path d="M2 14h2" />
      <path d="M20 14h2" />
      <path d="M15 13v2" />
      <path d="M9 13v2" />
    </LucideIcon>
  );
}
