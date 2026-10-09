import { QueryClient, type QueryKey } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";
import { keys } from "../queries/keys";
import { entityOf, parseEnvelope } from "./envelopes";
import type { Hints } from "./hints";
import { isKeptFresh, PUSHED_ENTITIES, replayKey, routeEnvelope } from "./router";

function recording() {
  const queryClient = new QueryClient();
  const seen: QueryKey[] = [];
  queryClient.invalidateQueries = (filters) => {
    seen.push(filters?.queryKey ?? []);
    return Promise.resolve();
  };
  return { queryClient, seen };
}

function pushOf(kind: string, extra: Record<string, unknown> = {}) {
  const envelope = parseEnvelope(
    JSON.stringify({
      type: "event",
      sent_at: null,
      topic: "entity_changed",
      payload: { kind, target_id: "x", seq: 3, actor_id: "u1", ...extra },
    }),
  );
  expect(envelope).not.toBeNull();
  return envelope!;
}

/** Every kind the service pushes on the entity_changed topic. */
const SERVER_KINDS = [
  "media.file.created",
  "media.file.updated",
  "media.file.deleted",
  "orchestrations.orchestration.created",
  "orchestrations.orchestration.updated",
  "tenancy.api_key.created",
  "tenancy.api_key.deleted",
  "tenancy.invitation.created",
  "tenancy.invitation.updated",
  "tenancy.membership.updated",
  "tenancy.session.revoked",
  "tenancy.user.created",
  "tenancy.user.updated",
  "tenancy.user.deleted",
];

/** Every key some query reads under, with one sample argument per factory. */
function queryKeys(node: unknown): QueryKey[] {
  if (typeof node === "function") return [(node as (arg: unknown) => QueryKey)("sample")];
  if (Array.isArray(node)) return [node as QueryKey];
  if (typeof node === "object" && node !== null) return Object.values(node).flatMap(queryKeys);
  return [];
}

const isPrefixOf = (prefix: QueryKey, key: QueryKey) =>
  prefix.length <= key.length && prefix.every((part, index) => Object.is(part, key[index]));

describe("routeEnvelope", () => {
  it("invalidates queries by the entity name inside the push's kind, ignoring unknown fields", () => {
    const { queryClient, seen } = recording();
    const envelope = parseEnvelope(
      JSON.stringify({
        type: "event",
        sent_at: null,
        topic: "entity_changed",
        payload: { kind: "tenancy.api_key.created", target_id: "x", seq: 3, added_later: 1 },
      }),
    );
    expect(envelope).not.toBeNull();
    expect(routeEnvelope(queryClient, envelope!)).toEqual({ invalidated: [keys.apiKeys.all] });
    expect(seen).toEqual([keys.apiKeys.all]);
  });

  it("refreshes the pending invitations when an invitation changes", () => {
    for (const kind of ["tenancy.invitation.created", "tenancy.invitation.updated"]) {
      const { queryClient, seen } = recording();
      expect(routeEnvelope(queryClient, pushOf(kind))).toEqual({ invalidated: [keys.invitations.all] });
      expect(seen.every((key) => isPrefixOf(key, keys.invitations.list(50)))).toBe(true);
    }
  });

  it("refreshes the org's storage usage when a file changes", () => {
    const { queryClient, seen } = recording();
    expect(routeEnvelope(queryClient, pushOf("media.file.updated"))).toEqual({ invalidated: [keys.files.all] });
    expect(seen.every((key) => isPrefixOf(key, keys.files.usage))).toBe(true);
  });

  it("refreshes who the user is, their places, and the member roles when a membership changes, since the role rides all three", () => {
    // A member demoted to viewer loses the controls a viewer may not use on
    // the push, not on the next reload.
    const { queryClient, seen } = recording();
    expect(routeEnvelope(queryClient, pushOf("tenancy.membership.updated"))).toEqual({
      invalidated: [keys.me, keys.myMemberships.all, keys.memberships.all],
    });
    expect(seen).toEqual([keys.me, keys.myMemberships.all, keys.memberships.all]);
  });

  it("refreshes who the user is when a user changes, since the name rides the me query", () => {
    // Renaming yourself from another client changes a row of the member list
    // and the name in the header; the header reads `me`.
    const { queryClient, seen } = recording();
    const outcome = routeEnvelope(queryClient, pushOf("tenancy.user.updated"));
    expect(outcome).toEqual({ invalidated: [keys.users.all, keys.me] });
    expect(seen).toEqual([keys.users.all, keys.me]);
  });

  it("hands a push about an entity with a reader to that reader, with its version, and invalidates nothing", () => {
    const { queryClient, seen } = recording();
    const hinted: [string, number | undefined][] = [];
    const reader: Hints = { hint: (id, version) => hinted.push([id, version]), stop: () => undefined };
    expect(routeEnvelope(queryClient, pushOf("tenancy.user.updated", { version: 4 }), { user: reader })).toEqual({
      invalidated: [],
      hinted: "user",
    });
    expect(routeEnvelope(queryClient, pushOf("tenancy.user.deleted"), { user: reader })).toEqual({
      invalidated: [],
      hinted: "user",
    });
    expect(hinted).toEqual([
      ["x", 4],
      ["x", undefined],
    ]);
    expect(seen).toEqual([]);
    // Every other entity is routed as before.
    expect(routeEnvelope(queryClient, pushOf("tenancy.api_key.created"), { user: reader })).toEqual({
      invalidated: [keys.apiKeys.all],
    });
  });

  it("invalidates nothing for a revoked session or an orchestration, which no query reads", () => {
    // This session's own revocation arrives as a 4401 close, not as a push.
    for (const kind of ["tenancy.session.revoked", "orchestrations.orchestration.updated"]) {
      const { queryClient, seen } = recording();
      expect(routeEnvelope(queryClient, pushOf(kind))).toEqual({ invalidated: [] });
      expect(seen).toEqual([]);
    }
  });

  it("routes every kind the server pushes to a key some query reads under", () => {
    const used = queryKeys(keys);
    for (const kind of SERVER_KINDS) {
      const { queryClient } = recording();
      for (const routed of routeEnvelope(queryClient, pushOf(kind)).invalidated) {
        expect(used.some((key) => isPrefixOf(routed, key)), `${kind} routes to ${JSON.stringify(routed)}`).toBe(true);
      }
    }
  });

  it("ignores frames that are not pushes and rejects malformed ones", () => {
    const queryClient = new QueryClient();
    const pong = parseEnvelope(JSON.stringify({ type: "pong", sent_at: null }));
    expect(routeEnvelope(queryClient, pong!)).toEqual({ invalidated: [] });
    expect(parseEnvelope("not json")).toBeNull();
    expect(parseEnvelope(JSON.stringify({ type: "mystery" }))).toBeNull();
    const bare = parseEnvelope(
      JSON.stringify({ type: "event", sent_at: null, topic: "entity_changed", payload: {} }),
    );
    expect(routeEnvelope(queryClient, bare!)).toEqual({ invalidated: [] });
  });
});

describe("isKeptFresh", () => {
  it("names every entity the server pushes", () => {
    expect([...new Set(SERVER_KINDS.map(entityOf))].sort()).toEqual([...PUSHED_ENTITIES].sort());
  });

  it("holds every query a push reaches, and no query no push names", () => {
    for (const kind of SERVER_KINDS) {
      const { queryClient } = recording();
      for (const routed of routeEnvelope(queryClient, pushOf(kind)).invalidated) {
        expect(isKeptFresh(routed), `${kind} reaches ${JSON.stringify(routed)}`).toBe(true);
      }
    }
    expect(isKeptFresh(keys.me)).toBe(true);
    expect(isKeptFresh(keys.memberships.list(200))).toBe(true);
    expect(isKeptFresh(keys.files.usage)).toBe(true);
    expect(isKeptFresh(keys.identity)).toBe(false);
  });
});

describe("replayKey", () => {
  it("keeps one push per record of an entity with a reader, and one per entity otherwise", () => {
    const reader: Hints = { hint: () => undefined, stop: () => undefined };
    const of = (kind: string, target: string) => {
      const envelope = pushOf(kind, { target_id: target });
      if (envelope.type !== "event") throw new Error("not a push");
      return envelope;
    };
    const readers = { user: reader };
    expect(replayKey(of("tenancy.user.updated", "a"), readers)).not.toBe(replayKey(of("tenancy.user.deleted", "b"), readers));
    expect(replayKey(of("tenancy.user.updated", "a"), readers)).toBe(replayKey(of("tenancy.user.deleted", "a"), readers));
    expect(replayKey(of("tenancy.api_key.created", "a"), readers)).toBe(replayKey(of("tenancy.api_key.deleted", "b"), readers));
    expect(replayKey(of("tenancy.user.updated", "a"))).toBe(replayKey(of("tenancy.user.updated", "b")));
  });
});
