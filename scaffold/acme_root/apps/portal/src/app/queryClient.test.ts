// The app's one retry is the transport client's. This pins the other half of
// that: the query library does not add a second round of attempts on top.
// And a query a push keeps fresh is not read again on focus or by the clock
// while the socket is open; with the socket down it is, as before.
import { QueryClient, type Query } from "@tanstack/react-query";
import { afterEach, describe, expect, it } from "vitest";
import { keys } from "../queries/keys";
import { useConnectionStore, type ConnectionStatus } from "../store/connection";
import { PUSHED_STALE_MS, queryClient, STALE_MS } from "./queryClient";

function queryUnder(queryKey: readonly unknown[]): Query {
  const client = new QueryClient();
  return client.getQueryCache().build(client, { queryKey });
}

function decided(queryKey: readonly unknown[]) {
  const query = queryUnder(queryKey);
  const { staleTime, refetchOnWindowFocus } = queryClient.getDefaultOptions().queries!;
  return {
    staleTime: typeof staleTime === "function" ? staleTime(query) : staleTime,
    onFocus: typeof refetchOnWindowFocus === "function" ? refetchOnWindowFocus(query) : refetchOnWindowFocus,
  };
}

const withSocket = (status: ConnectionStatus) => useConnectionStore.setState({ status });

afterEach(() => useConnectionStore.getState().close());

describe("the shared query cache", () => {
  it("does not retry, so the transport client's attempts are all there are", () => {
    const defaults = queryClient.getDefaultOptions();
    expect(defaults.queries?.retry).toBe(false);
    expect(defaults.mutations?.retry).toBe(false);
  });

  it("keeps a query a push reaches fresh for minutes, and off the focus refetch, while the socket is open", () => {
    withSocket("open");
    for (const key of [keys.tasks.open("team"), keys.me, keys.billing, keys.users.list(200), keys.myMemberships.list(50)]) {
      expect(decided(key), JSON.stringify(key)).toEqual({ staleTime: PUSHED_STALE_MS, onFocus: false });
    }
  });

  it("reads a query no push names as before, socket or not", () => {
    withSocket("open");
    expect(decided(keys.identity)).toEqual({ staleTime: STALE_MS, onFocus: true });
  });

  it("reads every query as before while the socket is not open", () => {
    for (const status of ["connecting", "degraded", "paused", "closed"] as const) {
      withSocket(status);
      expect(decided(keys.tasks.open("team")), status).toEqual({ staleTime: STALE_MS, onFocus: true });
      expect(decided(keys.me), status).toEqual({ staleTime: STALE_MS, onFocus: true });
    }
  });
});
