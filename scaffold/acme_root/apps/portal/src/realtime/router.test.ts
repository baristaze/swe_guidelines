import { QueryClient, type QueryKey } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";
import { keys } from "../queries/keys";
import { parseEnvelope } from "./envelopes";
import { entityOf } from "./envelopes";
import { isKeptFresh, PUSHED_ENTITIES, reminderOf, routeEnvelope } from "./router";

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
  "billing.account.created",
  "billing.account.updated",
  "tasks.task.created",
  "tasks.task.updated",
  "tasks.task.deleted",
  "tasks.task.restored",
  "tasks.task.archived",
  "media.file.created",
  "media.file.updated",
  "media.file.deleted",
  "tasks.task.reminded",
  "orchestrations.orchestration.updated",
  "slack.installation.created",
  "slack.installation.updated",
  "slack.installation.deleted",
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

  it("refreshes a task's files and the org's usage when a file changes", () => {
    const { queryClient, seen } = recording();
    expect(routeEnvelope(queryClient, pushOf("media.file.updated"))).toEqual({ invalidated: [keys.files.all] });
    expect(seen.every((key) => isPrefixOf(key, keys.files.ofTask("t1")) && isPrefixOf(key, keys.files.usage))).toBe(
      true,
    );
  });

  it("refreshes who the user is, their places, and the member roles when a membership changes, since the role rides all three", () => {
    // A user demoted to viewer loses the add, edit, and drag controls on the
    // push, not on the next reload.
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

  it("refreshes the org's plan when its billing account changes", () => {
    // A checkout paid in another tab, or a cancellation, changes the plan the
    // chip and the billing page show, on the push.
    for (const kind of ["billing.account.created", "billing.account.updated"]) {
      const { queryClient, seen } = recording();
      expect(routeEnvelope(queryClient, pushOf(kind))).toEqual({ invalidated: [keys.billing] });
      expect(seen).toEqual([keys.billing]);
    }
  });

  it("invalidates nothing for a revoked session, which no query reads", () => {
    // This session's own revocation arrives as a 4401 close, not as a push.
    const { queryClient, seen } = recording();
    expect(routeEnvelope(queryClient, pushOf("tenancy.session.revoked"))).toEqual({ invalidated: [] });
    expect(seen).toEqual([]);
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

  it("refreshes the task lists for a replayed task record, which stands for the records before it", () => {
    const { queryClient, seen } = recording();
    expect(routeEnvelope(queryClient, pushOf("tasks.task.reminded"))).toEqual({ invalidated: [keys.tasks.all] });
    expect(seen).toEqual([keys.tasks.all]);
  });

  it("hands a live task push to the hints, one per push, and invalidates no list", () => {
    for (const kind of ["tasks.task.created", "tasks.task.updated", "tasks.task.deleted", "tasks.task.restored", "tasks.task.archived", "tasks.task.reminded"]) {
      const { queryClient, seen } = recording();
      const hinted: string[] = [];
      expect(routeEnvelope(queryClient, pushOf(kind), { hint: (id) => hinted.push(id) })).toEqual({
        invalidated: [],
        hinted: ["x"],
      });
      expect(hinted).toEqual(["x"]);
      expect(seen).toEqual([]);
    }
  });

  it("hands the hints the version a task push names, and none when it names none or not a number", () => {
    const hinted: [string, number | undefined][] = [];
    const sink = { hint: (id: string, version?: number) => hinted.push([id, version]) };
    routeEnvelope(recording().queryClient, pushOf("tasks.task.updated", { version: 4 }), sink);
    routeEnvelope(recording().queryClient, pushOf("tasks.task.updated"), sink);
    routeEnvelope(recording().queryClient, pushOf("tasks.task.updated", { version: "4" }), sink);
    expect(hinted).toEqual([
      ["x", 4],
      ["x", undefined],
      ["x", undefined],
    ]);
  });

  it("refreshes an import's own record on its progress, and no task list", () => {
    // An import step lands a hundred tasks; each is its own task push. The
    // record's progress push reads the progress line, never the lists.
    const { queryClient, seen } = recording();
    const hinted: string[] = [];
    const outcome = routeEnvelope(queryClient, pushOf("orchestrations.orchestration.updated"), {
      hint: (id) => hinted.push(id),
    });
    expect(outcome).toEqual({ invalidated: [keys.imports.all] });
    expect(seen.some((key) => isPrefixOf(key, keys.tasks.open("team")) || isPrefixOf(key, keys.tasks.all))).toBe(false);
    expect(hinted).toEqual([]);
  });

  it("keeps every other kind on its invalidation when the hints are there", () => {
    const hinted: string[] = [];
    for (const kind of SERVER_KINDS.filter((k) => !k.startsWith("tasks."))) {
      const { queryClient } = recording();
      const withHints = routeEnvelope(queryClient, pushOf(kind), { hint: (id) => hinted.push(id) });
      expect(withHints).toEqual(routeEnvelope(recording().queryClient, pushOf(kind)));
    }
    expect(hinted).toEqual([]);
  });

  it("refreshes the Slack installation on each of its pushes", () => {
    for (const action of ["created", "updated", "deleted"]) {
      const { queryClient, seen } = recording();
      routeEnvelope(queryClient, pushOf(`slack.installation.${action}`));
      expect(seen).toHaveLength(1);
      expect(isPrefixOf(seen[0]!, keys.slack.installation)).toBe(true);
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

describe("reminderOf", () => {
  it("names the task a reminder push is about", () => {
    expect(reminderOf(pushOf("tasks.task.reminded"))).toBe("x");
  });

  it("is null for every other push and every other frame", () => {
    expect(reminderOf(pushOf("tasks.task.updated"))).toBeNull();
    expect(reminderOf(pushOf("slack.installation.updated"))).toBeNull();
    expect(reminderOf(parseEnvelope(JSON.stringify({ type: "pong", sent_at: null, seq: 1 }))!)).toBeNull();
    const other = parseEnvelope(
      JSON.stringify({
        type: "event",
        sent_at: null,
        topic: "presence",
        payload: { kind: "tasks.task.reminded", target_id: "x", seq: 3 },
      }),
    );
    expect(reminderOf(other!)).toBeNull();
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
    expect(isKeptFresh(keys.tasks.open("team"))).toBe(true);
    expect(isKeptFresh(keys.me)).toBe(true);
    expect(isKeptFresh(keys.billing)).toBe(true);
    expect(isKeptFresh(keys.identity)).toBe(false);
    expect(isKeptFresh(keys.files.preview("f1"))).toBe(false);
  });
});
