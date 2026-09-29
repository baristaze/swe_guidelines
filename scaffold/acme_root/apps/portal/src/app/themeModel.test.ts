import { describe, expect, it } from "vitest";
import { applyTheme, parseTheme, THEME_CHOICES } from "./themeModel";

describe("the theme", () => {
  it("offers system, light, and dark, in that order", () => {
    expect(THEME_CHOICES.map((choice) => choice.value)).toEqual(["system", "light", "dark"]);
    expect(THEME_CHOICES.map((choice) => choice.label)).toEqual(["System", "Light", "Dark"]);
  });

  it("reads anything it does not know as the system's", () => {
    expect(parseTheme("dark")).toBe("dark");
    expect(parseTheme("light")).toBe("light");
    expect(parseTheme("sepia")).toBe("system");
    expect(parseTheme(undefined)).toBe("system");
  });

  it("pins the root for light or dark and unpins it for system", () => {
    const root = { dataset: {} as DOMStringMap };
    applyTheme(root, "dark");
    expect(root.dataset.theme).toBe("dark");
    applyTheme(root, "light");
    expect(root.dataset.theme).toBe("light");
    applyTheme(root, "system");
    expect(root.dataset.theme).toBeUndefined();
  });
});
