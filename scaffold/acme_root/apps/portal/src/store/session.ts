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
