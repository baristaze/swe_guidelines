// Preferences only the UI knows about, kept across visits: which task list
// the person looked at last, the theme they picked (or the system's), and
// which sections of the task list they folded, per org. Local storage is the
// right place for a preference; the session token is the one thing that
// never goes there.
import type { TaskScope } from "../api";
import { parseTheme, type ThemePreference } from "../app/themeModel";
import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

export const PREFERENCES_STORAGE_KEY = "acme.portal.preferences";

/** An earlier build kept the scope under this key as a bare word. */
export const LEGACY_SCOPE_KEY = "acme.portal.taskScope";

/** A section of the task list: the open tasks, or the done ones. */
export type TaskSection = "open" | "done";

/** Which sections an org's task list has folded, by org id. An org the
 * person never folded or unfolded anything in has no entry. */
export type FoldedSections = Record<string, Partial<Record<TaskSection, boolean>>>;

/** How a section starts before the person folds or unfolds it: Open
 * unfolded, since it is the work, and Done folded, since it only grows. */
export const FOLDED_AT_FIRST: Record<TaskSection, boolean> = { open: false, done: true };

/** Pure: whether the section is folded in this org. */
export function isFolded(folded: FoldedSections, orgId: string | null, section: TaskSection): boolean {
  return (orgId === null ? undefined : folded[orgId]?.[section]) ?? FOLDED_AT_FIRST[section];
}

interface PreferencesState {
  taskScope: TaskScope;
  setTaskScope: (scope: TaskScope) => void;
  theme: ThemePreference;
  setTheme: (theme: ThemePreference) => void;
  folded: FoldedSections;
  setFolded: (orgId: string, section: TaskSection, folded: boolean) => void;
}

/** Pure: the scope an earlier build left as a bare word, and nothing else. */
export function legacyScope(value: string | null): TaskScope | null {
  return value === "team" || value === "mine" ? value : null;
}

/** Reads the scope an earlier build left under its own key, once, and drops
 * it; null when there is none or the storage cannot be read. */
export function adoptLegacyScope(storage: Pick<Storage, "getItem" | "removeItem"> | undefined): TaskScope | null {
  try {
    const scope = legacyScope(storage?.getItem(LEGACY_SCOPE_KEY) ?? null);
    storage?.removeItem(LEGACY_SCOPE_KEY);
    return scope;
  } catch {
    return null;
  }
}

const adopted = adoptLegacyScope(typeof localStorage === "undefined" ? undefined : localStorage);

export const usePreferencesStore = create<PreferencesState>()(
  persist(
    (set) => ({
      taskScope: adopted ?? "mine",
      setTaskScope: (taskScope) => set({ taskScope }),
      theme: "system",
      setTheme: (theme) => set({ theme }),
      folded: {},
      setFolded: (orgId, section, value) =>
        set((state) => ({ folded: { ...state.folded, [orgId]: { ...state.folded[orgId], [section]: value } } })),
    }),
    {
      name: PREFERENCES_STORAGE_KEY,
      storage: createJSONStorage(() => localStorage),
      // A theme the stored state does not name, or names wrongly, is the
      // system's; folds that are not an object are none.
      merge: (stored, current) => {
        const kept = (stored ?? {}) as Partial<PreferencesState>;
        const folded = kept.folded && typeof kept.folded === "object" ? kept.folded : {};
        return { ...current, ...kept, theme: parseTheme(kept.theme), folded };
      },
    },
  ),
);
