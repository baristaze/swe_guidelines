// Pure: the theme a person can pick, the choices the account menu offers,
// and how a choice reaches the page (an attribute on the root the theme's CSS
// reads).

/** "system" follows the operating system's light or dark mode. */
export type ThemePreference = "system" | "light" | "dark";

/** The account menu's choices, in the order it lists them. */
export const THEME_CHOICES: readonly { value: ThemePreference; label: string }[] = [
  { value: "system", label: "System" },
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
];

/** A stored value an older or a tampered build left is read as "system". */
export function parseTheme(value: unknown): ThemePreference {
  return value === "light" || value === "dark" ? value : "system";
}

/** Light or dark pins the page; system removes the pin. */
export function applyTheme(root: { dataset: DOMStringMap }, theme: ThemePreference): void {
  if (theme === "system") delete root.dataset.theme;
  else root.dataset.theme = theme;
}
