// Preferences only the UI knows about, kept across visits: the theme the
// person picked, or the system's. Local storage is the right place for a
// preference; the session token is the one thing that never goes there.
import { parseTheme, type ThemePreference } from "../app/themeModel";
import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

export const PREFERENCES_STORAGE_KEY = "acme.portal.preferences";

interface PreferencesState {
  theme: ThemePreference;
  setTheme: (theme: ThemePreference) => void;
}

export const usePreferencesStore = create<PreferencesState>()(
  persist(
    (set) => ({
      theme: "system",
      setTheme: (theme) => set({ theme }),
    }),
    {
      name: PREFERENCES_STORAGE_KEY,
      storage: createJSONStorage(() => localStorage),
      // A theme the stored state does not name, or names wrongly, is the
      // system's; a field the store does not know is dropped.
      merge: (stored, current) => {
        const kept = (stored ?? {}) as Partial<PreferencesState>;
        return { ...current, theme: parseTheme(kept.theme) };
      },
    },
  ),
);
