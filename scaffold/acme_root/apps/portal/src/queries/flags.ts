// The session's flags: one snapshot from the API, never from a flag
// provider. The server evaluates every flag for the session's org and
// person and answers the ones marked for clients. A flag here only shows or
// hides: the server checks it again on the operation it gates.
import { queryOptions, useQuery } from "@tanstack/react-query";
import type { FlagsView } from "@acme/client";
import { api } from "../app/api";
import { keys } from "./keys";

/** The flags this app reads. The server declares every flag; a name the
 * snapshot does not carry reads off. */
export type FlagName = "media-uploads";

/** How often an open tab reads the snapshot again, so a kill switch reaches
 * it without a reload. A hidden tab does not: it reads again when it is
 * shown. */
export const FLAGS_REFRESH_MS = 5 * 60_000;

/** The snapshot's query. Its answer carries an `ETag` and `Cache-Control:
 * no-cache`, so the browser asks again with `If-None-Match`, and a read that
 * finds nothing new is a `304` with no body. A switch drops it with every
 * other answer of the old tenant (adoptSession). */
export const flagsQuery = queryOptions({
  queryKey: keys.flags,
  queryFn: ({ signal }) => api.get<FlagsView>("/v1/flags", { signal }),
  refetchOnWindowFocus: true,
  refetchInterval: FLAGS_REFRESH_MS,
});

/** One flag's value in a snapshot: off until the snapshot arrives, and off
 * for a name it does not carry. */
export function flagOn(snapshot: FlagsView | undefined, flag: FlagName): boolean {
  return snapshot?.flags[flag] === true;
}

/** Reads the snapshot. The signed-in shell calls it, so the read starts once
 * the exchange is done and starts over in each org. */
export function useFlags() {
  return useQuery(flagsQuery);
}

/** The one way a view-model reads a flag. */
export function useFlag(flag: FlagName): boolean {
  const { data } = useQuery({ ...flagsQuery, select: (snapshot) => flagOn(snapshot, flag) });
  return data ?? false;
}
