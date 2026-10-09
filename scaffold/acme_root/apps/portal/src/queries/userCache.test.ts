// Members written into a real query cache from the reads a push leads to.
import { QueryClient, type InfiniteData } from "@tanstack/react-query";
import { ApiError, type MeView, type UserPageView, type UserView } from "@acme/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createHints, HINT_BURST, HINT_WINDOW_MS } from "../realtime/hints";
import { routeEnvelope } from "../realtime/router";
import { parseEnvelope } from "../realtime/envelopes";
import { keys } from "./keys";
import { placeInPages, removeFromPages, userHintEffects } from "./userCache";

type Pages = InfiniteData<UserPageView, string | null>;

const user = (id: string, name = id): UserView => ({
  id,
  display_name: name,
  email: `${id}@example.test`,
  created_at: "2026-10-01T00:00:00Z",
});

const pages = (...lists: [string[], string | null][]): Pages => ({
  pages: lists.map(([ids, next]) => ({ items: ids.map((id) => user(id)), next_cursor: next })),
  pageParams: lists.map((_, at) => (at === 0 ? null : `c${at}`)),
});

const idsOf = (data: Pages | undefined) => data?.pages.map((page) => page.items.map((item) => item.id));

const LIST = keys.users.list(200);

describe("placeInPages", () => {
  it("puts a member it holds in its own place", () => {
    const data = pages([["a", "c"], null]);
    const placed = placeInPages(data, user("c", "Cleo"));
    expect(idsOf(placed)).toEqual([["a", "c"]]);
    expect(placed.pages[0]!.items[1]!.display_name).toBe("Cleo");
  });

  it("puts a new member before the first larger id, on whichever page that is", () => {
    expect(idsOf(placeInPages(pages([["a", "c"], "c1"], [["e", "g"], null]), user("d")))).toEqual([
      ["a", "c"],
      ["d", "e", "g"],
    ]);
    expect(idsOf(placeInPages(pages([["a", "c"], "c1"], [["e"], null]), user("b")))).toEqual([["a", "b", "c"], ["e"]]);
  });

  it("puts a member past every id at the end once every page is in hand", () => {
    expect(idsOf(placeInPages(pages([["a"], "c1"], [["c"], null]), user("z")))).toEqual([["a"], ["c", "z"]]);
    expect(idsOf(placeInPages(pages([[], null]), user("a")))).toEqual([["a"]]);
  });

  it("leaves a member past the pages in hand to the walk, which reads it with a later page", () => {
    const data = pages([["a", "c"], "c1"]);
    expect(placeInPages(data, user("z"))).toBe(data);
  });
});

describe("removeFromPages", () => {
  it("takes the member out of the page that holds it, and leaves pages without it as they are", () => {
    const data = pages([["a", "c"], "c1"], [["e"], null]);
    expect(idsOf(removeFromPages(data, "c"))).toEqual([["a"], ["e"]]);
    expect(removeFromPages(data, "x")).toBe(data);
  });
});

describe("userHintEffects", () => {
  let queryClient: QueryClient;
  const me: MeView = {
    app: "portal",
    org: { id: "o1" } as MeView["org"],
    permissions: [],
    role: "owner",
    user: user("c", "Cleo"),
  } as MeView;

  beforeEach(() => {
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    queryClient.setQueryData<Pages>(LIST, pages([["a", "c"], null]));
    queryClient.setQueryData<MeView>(keys.me, me);
  });

  afterEach(() => queryClient.clear());

  const effectsOf = () => userHintEffects(queryClient, () => Promise.reject(new Error("not read here")));

  it("places a member in every member list, and in `me` when it is the caller", () => {
    const effects = effectsOf();
    effects.place(user("c", "Cleo B."), effects.stamp());
    effects.place(user("b"), effects.stamp());
    expect(idsOf(queryClient.getQueryData<Pages>(LIST))).toEqual([["a", "b", "c"]]);
    expect(queryClient.getQueryData<MeView>(keys.me)!.user.display_name).toBe("Cleo B.");
  });

  it("never lets a late answer overwrite one from a read issued after it", () => {
    const effects = effectsOf();
    const early = effects.stamp();
    const late = effects.stamp();
    effects.place(user("c", "New"), late);
    effects.place(user("c", "Old"), early);
    expect(queryClient.getQueryData<Pages>(LIST)!.pages[0]!.items[1]!.display_name).toBe("New");
    expect(queryClient.getQueryData<MeView>(keys.me)!.user.display_name).toBe("New");
    effects.remove("c", early);
    expect(idsOf(queryClient.getQueryData<Pages>(LIST))).toEqual([["a", "c"]]);
  });

  it("removes a member from every list and leaves `me`, whose session ends by itself", () => {
    const effects = effectsOf();
    const before = effects.stamp();
    effects.remove("c", effects.stamp());
    expect(idsOf(queryClient.getQueryData<Pages>(LIST))).toEqual([["a"]]);
    expect(queryClient.getQueryData<MeView>(keys.me)).toEqual(me);
    // A read issued before the removal never brings the member back.
    effects.place(user("c"), before);
    expect(idsOf(queryClient.getQueryData<Pages>(LIST))).toEqual([["a"]]);
  });

  it("places a member again once a list being read when it was placed lands", async () => {
    const effects = effectsOf();
    let land!: (page: UserPageView) => void;
    const reading = queryClient.fetchInfiniteQuery({
      queryKey: LIST,
      initialPageParam: null as string | null,
      getNextPageParam: (last: UserPageView) => last.next_cursor,
      queryFn: () => new Promise<UserPageView>((resolve) => (land = resolve)),
    });
    effects.place(user("b"), effects.stamp());
    expect(idsOf(queryClient.getQueryData<Pages>(LIST))).toEqual([["a", "b", "c"]]);
    // The read was answered before the change: it holds no `b`.
    land({ items: [user("a"), user("c")], next_cursor: null });
    await reading;
    expect(idsOf(queryClient.getQueryData<Pages>(LIST))).toEqual([["a", "b", "c"]]);
  });

  it("reads again a query under the entity's key whose shape says nothing of where a member goes", () => {
    const effects = effectsOf();
    queryClient.setQueryData(["user", "count"], 2);
    effects.place(user("b"), effects.stamp());
    expect(queryClient.getQueryState(["user", "count"])!.isInvalidated).toBe(true);
    expect(queryClient.getQueryState(LIST)!.isInvalidated).toBe(false);
  });

  it("reads the member list and `me` again when asked for the collections", () => {
    effectsOf().readCollections();
    expect(queryClient.getQueryState(LIST)!.isInvalidated).toBe(true);
    expect(queryClient.getQueryState(keys.me)!.isInvalidated).toBe(true);
  });

  it("takes a 404 for a member not there to see, and nothing else", () => {
    const effects = effectsOf();
    expect(effects.isGone(new ApiError(404, "not_found", "no", null))).toBe(true);
    expect(effects.isGone(new ApiError(503, "unavailable", "no", null))).toBe(false);
    expect(effects.isGone(new Error("offline"))).toBe(false);
  });
});

describe("a push about a user", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  const pushOf = (target: string, kind = "tenancy.user.updated") =>
    parseEnvelope(
      JSON.stringify({ type: "event", sent_at: null, topic: "entity_changed", payload: { kind, target_id: target, seq: 1, actor_id: "u1" } }),
    )!;

  it("reads that one member and places it, or takes it out when the read finds nothing", async () => {
    const queryClient = new QueryClient();
    queryClient.setQueryData<Pages>(LIST, pages([["a", "c"], null]));
    const reads: string[] = [];
    const read = (id: string) => {
      reads.push(id);
      return id === "c" ? Promise.reject(new ApiError(404, "not_found", "no", null)) : Promise.resolve(user(id, "Read"));
    };
    const readers = { user: createHints(userHintEffects(queryClient, read)) };
    expect(routeEnvelope(queryClient, pushOf("b", "tenancy.user.created"), readers)).toEqual({
      invalidated: [],
      hinted: "user",
    });
    routeEnvelope(queryClient, pushOf("b"), readers);
    routeEnvelope(queryClient, pushOf("c", "tenancy.user.deleted"), readers);
    await vi.advanceTimersByTimeAsync(HINT_WINDOW_MS);
    // Two pushes about one member in one window are one read.
    expect(reads).toEqual(["b", "c"]);
    expect(idsOf(queryClient.getQueryData<Pages>(LIST))).toEqual([["a", "b"]]);
    expect(queryClient.getQueryState(LIST)!.isInvalidated).toBe(false);
    readers.user.stop();
  });

  it("in a burst reads the list once instead of member by member", async () => {
    const queryClient = new QueryClient();
    queryClient.setQueryData<Pages>(LIST, pages([["a"], null]));
    const read = vi.fn((id: string) => Promise.resolve(user(id)));
    const readers = { user: createHints(userHintEffects(queryClient, read)) };
    for (let n = 0; n <= HINT_BURST; n += 1) routeEnvelope(queryClient, pushOf(`m${n}`), readers);
    await vi.advanceTimersByTimeAsync(HINT_WINDOW_MS);
    expect(read).not.toHaveBeenCalled();
    expect(queryClient.getQueryState(LIST)!.isInvalidated).toBe(true);
    readers.user.stop();
  });
});

describe("a switch of org", () => {
  const listRead = (queryClient: QueryClient, queryFn: () => Promise<UserPageView>) =>
    queryClient.fetchInfiniteQuery({
      queryKey: LIST,
      initialPageParam: null as string | null,
      getNextPageParam: (last: UserPageView) => last.next_cursor,
      queryFn,
    });

  // The list read under the old org is in flight when a push places one of
  // its members; the read then fails, or the switch abandons it. The new org's
  // list is read under the same key.
  it.each([
    ["failed", () => Promise.reject(new Error("offline"))],
    ["abandoned", () => new Promise<UserPageView>(() => {})],
  ])("shows none of the old org's members in the new org's list when the read under the old one %s", async (_, queryFn) => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const effects = userHintEffects(queryClient, () => Promise.reject(new Error("not read here")));
    const reader = createHints(effects);
    void listRead(queryClient, queryFn).catch(() => undefined);
    effects.place(user("b", "Old org's member"), effects.stamp());
    await new Promise((settled) => setTimeout(settled, 0));

    // The session ends: its reader stops, and the cache of the old org goes.
    reader.stop();
    queryClient.clear();

    await listRead(queryClient, () => Promise.resolve({ items: [user("x")], next_cursor: null }));
    expect(idsOf(queryClient.getQueryData<Pages>(LIST))).toEqual([["x"]]);
    queryClient.clear();
  });
});
