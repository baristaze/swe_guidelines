import { ApiError, type EventView } from "../api";
import { describe, expect, it } from "vitest";
import {
  behind,
  eventEnvelope,
  FIRST_CATCH_UP_MARGIN_MS,
  isLastPage,
  place,
  readsBegan,
  tailSince,
  truncatedHead,
} from "./stream";

describe("place", () => {
  it("takes the first sequenced push as the cursor", () => {
    expect(place(null, 7)).toEqual({ kind: "next", cursor: 7 });
  });

  it("advances on the next seq and drops what was already applied", () => {
    expect(place(7, 8)).toEqual({ kind: "next", cursor: 8 });
    expect(place(7, 7)).toEqual({ kind: "seen" });
    expect(place(7, 3)).toEqual({ kind: "seen" });
  });

  it("names the replay point when a seq is skipped", () => {
    expect(place(7, 9)).toEqual({ kind: "gap", after: 7 });
    expect(place(7, 100)).toEqual({ kind: "gap", after: 7 });
  });
});

describe("behind", () => {
  it("names the replay point when the head the pong reports is past the cursor", () => {
    expect(behind(7, 9)).toBe(7);
    expect(behind(7, 8)).toBe(7);
  });

  it("stays quiet when caught up, and before the first cursor", () => {
    expect(behind(7, 7)).toBeNull();
    expect(behind(7, 3)).toBeNull();
    expect(behind(null, 9)).toBeNull();
  });
});

describe("eventEnvelope", () => {
  it("turns a replayed record into the envelope the socket would have carried", () => {
    const event: EventView = {
      seq: 4,
      kind: "tasks.task.updated",
      target_id: "t1",
      produced_at: "2026-09-16T12:00:00Z",
      actor_id: "u1",
    };
    expect(eventEnvelope(event)).toEqual({
      type: "event",
      topic: "entity_changed",
      sent_at: null,
      payload: { kind: "tasks.task.updated", target_id: "t1", seq: 4, actor_id: "u1" },
    });
  });

  it("ends paging on a short page", () => {
    expect(isLastPage(200, 200)).toBe(false);
    expect(isLastPage(199, 200)).toBe(true);
    expect(isLastPage(0, 200)).toBe(true);
  });
});

describe("the first catch-up's window", () => {
  const at = (seq: number, time: string): EventView => ({
    seq,
    kind: "tasks.task.updated",
    target_id: `t${seq}`,
    produced_at: `2026-09-16T${time}Z`,
    actor_id: "u1",
  });
  const noon = Date.parse("2026-09-16T12:00:00Z");

  it("starts the margin before the server's time the page began reading", () => {
    expect(readsBegan("2026-09-16T12:00:03Z", 3000)).toBe(noon - FIRST_CATCH_UP_MARGIN_MS);
  });

  it("keeps the records produced since then", () => {
    const tail = [at(3, "11:59:00"), at(4, "12:00:01")];
    expect(tailSince(tail, 2, noon)).toEqual([at(4, "12:00:01")]);
  });

  it("cannot tell when every record of a tail read past the stream's start is recent", () => {
    expect(tailSince([at(3, "12:00:01")], 2, noon)).toBeNull();
    // From the start, the whole stream is in hand.
    expect(tailSince([at(1, "12:00:01")], 0, noon)).toEqual([at(1, "12:00:01")]);
    expect(tailSince([], 2, noon)).toEqual([]);
  });
});

describe("truncatedHead", () => {
  it("names the head only for a refusal that says the stream is trimmed", () => {
    const gone = new ApiError(410, "stream_truncated", "gone", null, undefined, null, { floor: 7, head: 9 });
    expect(truncatedHead(gone)).toBe(9);
    expect(truncatedHead(new ApiError(410, "stream_truncated", "gone", null))).toBeNull();
    expect(truncatedHead(new ApiError(503, "unavailable", "later", null))).toBeNull();
    expect(truncatedHead(new Error("offline"))).toBeNull();
  });
});
