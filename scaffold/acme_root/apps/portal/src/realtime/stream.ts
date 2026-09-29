// Pure: where a push sits relative to the last stream position the client
// saw, and how a replayed record becomes the envelope it would have been.
// The socket is a hint; the stream in storage is the truth, so a gap is
// closed by fetching after the cursor rather than by trusting the frame.
import { ApiError, type EventView } from "../api";
import type { EventEnvelope } from "./envelopes";

// The last contiguous seq the client applied; null until the first push.
export type Cursor = number | null;

export type Placement =
  | { kind: "next"; cursor: number } // in order: route it and advance
  | { kind: "seen" } // at or behind the cursor: already applied, drop it
  | { kind: "gap"; after: number }; // ahead of the cursor: replay after it first, never skip

export function place(cursor: Cursor, seq: number): Placement {
  if (cursor === null) return { kind: "next", cursor: seq };
  if (seq <= cursor) return { kind: "seen" };
  if (seq === cursor + 1) return { kind: "next", cursor: seq };
  return { kind: "gap", after: cursor };
}

// Where to replay from when the head the server reports is past the cursor;
// null when the client is caught up or has no cursor to replay from yet.
export function behind(cursor: Cursor, head: number): number | null {
  if (cursor === null || head <= cursor) return null;
  return cursor;
}

// The head to go on from when a replay was refused because the stream is
// trimmed past the cursor; null for any other failure. No page can close that
// gap, so the client reads afresh what it shows and resumes from the head.
export function truncatedHead(error: unknown): number | null {
  if (!(error instanceof ApiError) || error.code !== "stream_truncated") return null;
  return error.stream?.head ?? null;
}

export function eventEnvelope(event: EventView): EventEnvelope {
  return {
    type: "event",
    topic: "entity_changed",
    sent_at: null,
    payload: {
      kind: event.kind,
      target_id: event.target_id,
      seq: event.seq,
      actor_id: event.actor_id,
    },
  };
}

// A page shorter than the limit is the last one.
export function isLastPage(received: number, limit: number): boolean {
  return received < limit;
}

// How far before the page began reading its first catch-up looks. A write's
// record is stamped inside its transaction, and the commit that makes it
// visible can trail the stamp by up to a statement's ten-second deadline, so
// the margin is past it.
export const FIRST_CATCH_UP_MARGIN_MS = 15_000;

// The server's time, in epoch milliseconds, at which the page began reading,
// less the margin: the hello's `sent_at` is the server's clock, and the time
// the tab waited for it is taken off. The wait as measured here includes the
// frame's trip, so the answer errs early, which reads more and never less.
export function readsBegan(helloSentAt: string, waitedMs: number, marginMs = FIRST_CATCH_UP_MARGIN_MS): number {
  return Date.parse(helloSentAt) - waitedMs - marginMs;
}

// Of the stream's tail, the records produced since `since`: what a page that
// began reading then may not have seen. Null when the tail cannot tell: it
// was read from a seq past the stream's start and its first record is
// already that recent, so older records since then may lie before it.
export function tailSince(tail: EventView[], after: number, since: number): EventView[] | null {
  const recent = tail.filter((event) => Date.parse(event.produced_at) >= since);
  if (after > 0 && tail.length > 0 && recent.length === tail.length) return null;
  return recent;
}
