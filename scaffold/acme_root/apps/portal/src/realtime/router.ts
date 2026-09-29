// Envelopes route into the query cache, never into components. An
// `entity_changed` push invalidates the queries that carry the entity named
// inside its kind (`<namespace>.<entity>.<action>`): by convention the queries
// whose key starts with the entity name, and by the table below where the
// entity is read through another query, or through none.
import type { QueryClient, QueryKey } from "@tanstack/react-query";
import { keys } from "../queries/keys";
import { entityOf, isEntityChanged, type Envelope } from "./envelopes";

export interface RouteOutcome {
  invalidated: QueryKey[];
}

// Entities the convention does not reach on its own. A membership is read as
// the role and the permissions inside `me`, as the role in the person's list
// of places the org chip reads, and as the role beside each member in
// Settings; a user is read twice, as a row of the member list and as the name
// and the email in `me`, so a user push refreshes both. A revoked session is
// nobody's query: this session's own revocation arrives as a 4401 close, not
// as a push. No screen reads an orchestration's record; a screen that does
// keys its queries under the entity and drops its line here.
const CARRIED_BY: Readonly<Record<string, readonly QueryKey[]>> = {
  membership: [keys.me, keys.myMemberships.all, keys.memberships.all],
  user: [keys.users.all, keys.me],
  session: [],
  orchestration: [],
};

function targetsOf(entity: string): readonly QueryKey[] {
  return CARRIED_BY[entity] ?? [[entity]];
}

// Every entity the server pushes on the channel. The router test holds it to
// the kinds the service sends.
export const PUSHED_ENTITIES = [
  "api_key",
  "file",
  "invitation",
  "membership",
  "orchestration",
  "session",
  "user",
] as const;

const KEPT_FRESH = new Set(PUSHED_ENTITIES.flatMap((entity) => targetsOf(entity).map((key) => key[0])));

/** Whether a push reaches the query under this key: its first element is a
 * pushed entity, or a key the table above names. While the socket is open
 * such a query is as fresh as the last push, so it is not read again on its
 * own (`queryClient.ts`). A query no push names (the person's identity) is
 * not. */
export function isKeptFresh(queryKey: QueryKey): boolean {
  return KEPT_FRESH.has(queryKey[0] as string);
}

/** Routes one envelope, live or read back from the stream: every query its
 * entity is read from is read again. */
export function routeEnvelope(queryClient: QueryClient, envelope: Envelope): RouteOutcome {
  if (!isEntityChanged(envelope)) return { invalidated: [] };
  const targets = [...targetsOf(entityOf(envelope.payload.kind))];
  for (const queryKey of targets) void queryClient.invalidateQueries({ queryKey });
  return { invalidated: targets };
}
