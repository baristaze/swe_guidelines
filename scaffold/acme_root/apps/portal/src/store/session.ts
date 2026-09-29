// Client state only the UI knows about: the session token and who it is for.
// The bearer lives in memory and in the tab's session storage, so a reload
// survives and a closed tab forgets; never in local storage, which every tab
// and every later visit reads.
import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

export const SESSION_STORAGE_KEY = "acme.portal.session";

interface SessionState {
  token: string | null;
  orgSlug: string | null;
  setSession: (token: string, orgSlug: string) => void;
  clear: () => void;
}

/** An earlier build persisted the session in local storage; a token it left
 * there is dropped on load, never carried over. Storage may be unavailable
 * (a private window, a blocked origin), in which case there is nothing to drop. */
export function dropLegacyLocalSession(storage: Pick<Storage, "removeItem"> | undefined): void {
  try {
    storage?.removeItem(SESSION_STORAGE_KEY);
  } catch {
    // Nothing to drop.
  }
}

dropLegacyLocalSession(typeof localStorage === "undefined" ? undefined : localStorage);

export const useSessionStore = create<SessionState>()(
  persist(
    (set) => ({
      token: null,
      orgSlug: null,
      setSession: (token, orgSlug) => set({ token, orgSlug }),
      clear: () => set({ token: null, orgSlug: null }),
    }),
    { name: SESSION_STORAGE_KEY, storage: createJSONStorage(() => sessionStorage) },
  ),
);
