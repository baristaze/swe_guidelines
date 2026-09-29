// Design tokens: the operator console imports these from the portal.
// A colour or a shadow is a CSS custom property, so one inline style follows
// the theme: `theme.css` gives each property its light and its dark value.
const v = (name: string) => `var(--acme-${name})`;

export const tokens = {
  color: {
    /** The page behind the cards. */
    bg: v("bg"),
    /** A card, a field, a menu. */
    surface: v("surface"),
    /** A hovered row, a pill, the segmented control's track. */
    surfaceSubtle: v("surface-subtle"),
    text: v("text"),
    muted: v("muted"),
    border: v("border"),
    /** The edge of a field and of a checkbox: a step stronger than a card's. */
    borderStrong: v("border-strong"),
    /** The fill of a primary button, a checked box, the drop line. */
    accent: v("accent"),
    /** Text on the accent fill. */
    accentText: v("accent-text"),
    /** A tint of the accent, behind accent text. */
    accentSoft: v("accent-soft"),
    /** The accent as text on a surface: a link, an assignee. */
    link: v("link"),
    /** The fill of a destructive button. */
    danger: v("danger"),
    /** What went wrong, as text on a surface. */
    dangerText: v("danger-text"),
    warningBg: v("warning-bg"),
    warningText: v("warning-text"),
    warningBorder: v("warning-border"),
    live: v("live"),
    pending: v("pending"),
    tooltipBg: v("tooltip-bg"),
    tooltipText: v("tooltip-text"),
    focus: v("focus"),
  },
  shadow: { sm: v("shadow-sm"), md: v("shadow-md"), lg: v("shadow-lg") },
  // An 8px rhythm; xs is the half step inside a control.
  space: { xs: "4px", sm: "8px", md: "16px", lg: "24px", xl: "40px" },
  radius: { sm: "6px", md: "8px", lg: "12px" },
  font: {
    family: '"Inter Variable", Inter, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
    mono: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
    size: { xs: "12px", sm: "13px", md: "14px", lg: "16px", xl: "24px" },
  },
} as const;
