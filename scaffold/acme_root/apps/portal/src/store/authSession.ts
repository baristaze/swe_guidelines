// Client state only the UI knows about: the auth session token and who it is for.
// The bearer lives in memory and in the tab's session storage, so a reload
// survives and a closed tab forgets; never in local storage, which every tab
// and every later visit reads.
import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

export const AUTH_SESSION_STORAGE_KEY = "acme.portal.authSession";

interface AuthSessionState {
  token: string | null;
  orgSlug: string | null;
  setAuthSession: (token: string, orgSlug: string) => void;
  clear: () => void;
}

export const useAuthSessionStore = create<AuthSessionState>()(
  persist(
    (set) => ({
      token: null,
      orgSlug: null,
      setAuthSession: (token, orgSlug) => set({ token, orgSlug }),
      clear: () => set({ token: null, orgSlug: null }),
    }),
    { name: AUTH_SESSION_STORAGE_KEY, storage: createJSONStorage(() => sessionStorage) },
  ),
);
