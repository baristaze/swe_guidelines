// The members in the query cache, written from the one read a push about a
// user leads to. A member is shown in the member list and, for the caller, in
// `me`. The list is read in pages ordered by id, so where a member goes is
// always known: in its own place, before the first larger id, or at the end
// once every page is in hand; past the pages in hand, the walk brings it. A
// query under the entity's key whose shape this does not know is read again.
//
// A query being read while a member is placed may answer with what it read
// before the change, and a page read is added to the pages as they were when
// it started. So the member is placed again once that read lands, as the
// newest answer heard of it then. A query keeps its key across a switch of
// org, so when the session ends, every answer heard and every query watched
// goes with it.
import type { InfiniteData, Query, QueryClient, QueryKey } from "@tanstack/react-query";
import { ApiError, type MeView, type UserPageView, type UserView } from "@acme/client";
import type { HintEffects } from "../realtime/hints";
import { keys } from "./keys";
import { createLedger } from "./ledger";

type Pages = InfiniteData<UserPageView, string | null>;

function isPages(data: unknown): data is Pages {
  return typeof data === "object" && data !== null && Array.isArray((data as Partial<Pages>).pages);
}

/** The pages with the member in its place; the same pages when it has none
 * among them yet. */
export function placeInPages(data: Pages, user: UserView): Pages {
  const held = data.pages.findIndex((page) => page.items.some((item) => item.id === user.id));
  if (held >= 0) {
    const page = data.pages[held]!;
    const items = page.items.map((item) => (item.id === user.id ? user : item));
    return { ...data, pages: data.pages.map((other, at) => (at === held ? { ...page, items } : other)) };
  }
  for (const [at, page] of data.pages.entries()) {
    const before = page.items.findIndex((item) => item.id > user.id);
    if (before < 0) continue;
    const items = [...page.items.slice(0, before), user, ...page.items.slice(before)];
    return { ...data, pages: data.pages.map((other, index) => (index === at ? { ...page, items } : other)) };
  }
  const last = data.pages.at(-1);
  if (!last || last.next_cursor !== null) return data;
  const items = [...last.items, user];
  return { ...data, pages: data.pages.map((page) => (page === last ? { ...last, items } : page)) };
}

/** The pages without the member; the same pages when none holds it. */
export function removeFromPages(data: Pages, id: string): Pages {
  if (!data.pages.some((page) => page.items.some((item) => item.id === id))) return data;
  return { ...data, pages: data.pages.map((page) => ({ ...page, items: page.items.filter((item) => item.id !== id) })) };
}

const UNDECIDED = Symbol("undecided");

/** One query's data with the member written in, or UNDECIDED when its shape
 * does not say where the member goes. */
function rewrite(queryKey: QueryKey, data: unknown, id: string, user: UserView | null): unknown {
  if (queryKey[0] === keys.me[0]) {
    const me = data as MeView;
    return user && me.user.id === id ? { ...me, user } : me;
  }
  if (!isPages(data)) return UNDECIDED;
  return user ? placeInPages(data, user) : removeFromPages(data, id);
}

/** What the hint reader reaches for to read a member and place it, with a
 * ledger of its own, so a late answer never overwrites a newer one. */
export function userHintEffects(
  queryClient: QueryClient,
  read: (id: string) => Promise<UserView>,
): HintEffects<UserView> {
  const cache = queryClient.getQueryCache();
  const ledger = createLedger();
  // The newest answer heard of each member: the member, or null when gone.
  const heard = new Map<string, UserView | null>();
  // The members placed into a query while it was being read, by its hash.
  const pending = new Map<string, Set<string>>();
  let unsubscribe: (() => void) | null = null;

  const writeInto = (query: Query, id: string) => {
    const user = heard.get(id);
    if (user === undefined) return;
    const data = query.state.data;
    if (data !== undefined) {
      const changed = rewrite(query.queryKey, data, id, user);
      if (changed === UNDECIDED) {
        void queryClient.invalidateQueries({ queryKey: query.queryKey, exact: true });
        return;
      }
      if (changed !== data) queryClient.setQueryData(query.queryKey, changed);
    }
    if (query.state.fetchStatus === "fetching") hold(query.queryHash, id);
  };

  const hold = (queryHash: string, id: string) => {
    const ids = pending.get(queryHash) ?? new Set<string>();
    ids.add(id);
    pending.set(queryHash, ids);
    unsubscribe ??= cache.subscribe((event) => {
      if (event.type !== "updated" || event.action.type !== "success" || event.action.manual) return;
      const landed = pending.get(event.query.queryHash);
      if (!landed) return;
      pending.delete(event.query.queryHash);
      for (const member of landed) writeInto(event.query, member);
      if (pending.size === 0) {
        unsubscribe?.();
        unsubscribe = null;
      }
    });
  };

  const write = (id: string, user: UserView | null) => {
    heard.set(id, user);
    const queries = [...cache.findAll({ queryKey: keys.users.all }), ...cache.findAll({ queryKey: keys.me, exact: true })];
    for (const query of queries) writeInto(query, id);
  };

  return {
    read,
    isGone: (cause) => cause instanceof ApiError && cause.status === 404,
    holds: (id, version) => ledger.holds(id, version),
    stamp: () => ledger.stamp(),
    place(user, since) {
      if (ledger.admit(user.id, null, since)) write(user.id, user);
    },
    remove(id, since) {
      if (ledger.admitGone(id, since)) write(id, null);
    },
    readCollections() {
      void queryClient.invalidateQueries({ queryKey: keys.users.all });
      void queryClient.invalidateQueries({ queryKey: keys.me, exact: true });
    },
    stop() {
      unsubscribe?.();
      unsubscribe = null;
      pending.clear();
      heard.clear();
    },
  };
}
