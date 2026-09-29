// The one query cache the whole app shares. It lives outside the component
// tree so that forgetting a session can empty it from anywhere.
import { MutationCache, QueryClient, type Query } from "@tanstack/react-query";
import { isKeptFresh } from "../realtime/router";
import { useConnectionStore } from "../store/connection";
import { offerUpgrade } from "../store/upgrade";

/** How long an answer is fresh when nothing pushes about it. */
export const STALE_MS = 10_000;

/** How long an answer a push keeps fresh stays fresh while the socket is
 * open. A change reaches it as a push, and a push the socket missed as the
 * replay the reconnect makes, so this is a backstop, not the way it learns. */
export const PUSHED_STALE_MS = 5 * 60_000;

/** Whether the channel keeps this query fresh right now: a push reaches it
 * and the socket is open. While it is not (connecting, degraded, paused,
 * closed) the query is read the way it always is, on its own clock and on
 * focus, so the focus that ends a hidden tab's pause reads it even if the
 * socket is slow to return. */
export function keptFreshNow(query: Query): boolean {
  return useConnectionStore.getState().status === "open" && isKeptFresh(query.queryKey);
}

// No retry here. The app's one retry is in the transport client
// (src/api/client.ts), which knows the method, the idempotency key, and the
// failure, and a retry above one that already ran would multiply the calls a
// failing API sees. Mutations are the same rule said twice, since a write
// this layer repeated would not carry the key that makes it safe.
//
// Every write passes the mutation cache, so it is the one place a refusal for
// a plan's bound is turned into the upgrade dialog: a view-model that says a
// failure itself skips this one (`isPlanLimit`), and the person sees the
// plans, not a notice.
//
// A query a push keeps fresh is not read again when the tab regains focus,
// nor when it mounts within five minutes, while the socket is open: the push
// already said whether it changed. Both are decided when they are asked, so
// a socket that drops brings back the ten seconds and the focus refetch at
// once.
export const queryClient = new QueryClient({
  mutationCache: new MutationCache({ onError: (error) => void offerUpgrade(error) }),
  defaultOptions: {
    queries: {
      staleTime: (query) => (keptFreshNow(query) ? PUSHED_STALE_MS : STALE_MS),
      refetchOnWindowFocus: (query) => !keptFreshNow(query),
      retry: false,
    },
    mutations: { retry: false },
  },
});
