// Taking up a new session is one act too: every answer fetched under the
// old one goes, and the new token replaces the old in one write. Signing in,
// signing up, and switching orgs all land here, so no cache of the old tenant
// is ever shown under the new one, and no moment passes with no token held:
// a signed-in page never sees the tab signed out on its way to the new
// session. The signed-in shell is keyed by the org (routes.tsx), so the new
// org mounts it afresh: every screen reads again under the new session, and
// the realtime provider opens a new socket.
import type { IssuedSessionView } from "../api";
import { useSessionStore } from "../store/session";
import { queryClient } from "./queryClient";

export function adoptSession(issued: Pick<IssuedSessionView, "token" | "org">): void {
  queryClient.clear();
  useSessionStore.getState().setSession(issued.token, issued.org.slug);
}
