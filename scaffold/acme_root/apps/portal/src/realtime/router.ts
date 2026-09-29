// Envelopes route into the query cache, never into components. An
// `entity_changed` push invalidates the queries that carry the entity named
// inside its kind: by convention the queries whose key starts with the entity
// name, and by the table below where the entity is read through another
// query, or through none. A task is the exception: a live push about one
// reads that one task and places it into the lists (`taskHints.ts`), since a
// tick would otherwise read both whole lists in every open tab.
import type { QueryClient, QueryKey } from "@tanstack/react-query";
import { keys } from "../queries/keys";
import { entityOf, isEntityChanged, type Envelope } from "./envelopes";

export interface RouteOutcome {
  invalidated: QueryKey[];
  /** The tasks handed to the hints, to be read one by one. */
  hinted?: string[];
}

/** Where a live push about a task goes, with the version its change wrote
 * when the push names one; see `taskHints.ts`. */
export interface TaskHintSink {
  hint(id: string, version?: number): unknown;
}

/** The entity whose pushes are read one record at a time. */
export const TASK_ENTITY = keys.tasks.all[0];

// Entities the convention does not reach on its own. A membership is read as
// the role and the permissions inside `me`, as the role in the person's
// list of places the org chip reads, and as the role beside each member in
// Settings; a user is read twice, as a row of
// the member list and as the name and the email in `me`, so a user push
// refreshes both; a revoked session is nobody's query, and this session's own
// revocation arrives as a 4401 close, not as a push. A billing account is
// read as the org's plan, with the seats and the active tasks beside it.
const CARRIED_BY: Readonly<Record<string, readonly QueryKey[]>> = {
  account: [keys.billing],
  membership: [keys.me, keys.myMemberships.all, keys.memberships.all],
  user: [keys.users.all, keys.me],
  session: [],
};

function targetsOf(entity: string): readonly QueryKey[] {
  return CARRIED_BY[entity] ?? [[entity]];
}

// Every entity the server pushes on the channel. The router test holds it to
// the kinds the service sends.
export const PUSHED_ENTITIES = [
  "account",
  "api_key",
  "file",
  "installation",
  "invitation",
  "membership",
  "orchestration",
  "session",
  "task",
  "user",
] as const;

const KEPT_FRESH = new Set(PUSHED_ENTITIES.flatMap((entity) => targetsOf(entity).map((key) => key[0])));

/** Whether a push reaches the query under this key: its first element is a
 * pushed entity, or a key the table above names. While the socket is open
 * such a query is as fresh as the last push, so it is not read again on its
 * own (`queryClient.ts`). A query no push names (the person's identity, a
 * file's signed preview) is not. */
export function isKeptFresh(queryKey: QueryKey): boolean {
  return KEPT_FRESH.has(queryKey[0] as string);
}

/** Routes one envelope. With `tasks`, a push about a task is read as that
 * one task; without it (a replay, whose records are coalesced one per entity)
 * the task lists are read again, as every other entity's queries are. */
export function routeEnvelope(queryClient: QueryClient, envelope: Envelope, tasks?: TaskHintSink): RouteOutcome {
  if (!isEntityChanged(envelope)) return { invalidated: [] };
  const entity = entityOf(envelope.payload.kind);
  if (tasks && entity === TASK_ENTITY) {
    const { target_id: id, version } = envelope.payload;
    tasks.hint(id, typeof version === "number" ? version : undefined);
    return { invalidated: [], hinted: [id] };
  }
  const targets = [...targetsOf(entity)];
  for (const queryKey of targets) void queryClient.invalidateQueries({ queryKey });
  return { invalidated: targets };
}

// A reminder is the one push a person is told about as well as refreshed by:
// the task is read and placed as above, and this names the task to announce. Null for every other frame.
export const REMINDED_KIND = "tasks.task.reminded";

export function reminderOf(envelope: Envelope): string | null {
  if (!isEntityChanged(envelope) || envelope.payload.kind !== REMINDED_KIND) return null;
  return envelope.payload.target_id;
}
