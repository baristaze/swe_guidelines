// The socket loop over a fake socket and fake timers: what it sends, when it
// reconnects, and how it moves the stream cursor.
import { ApiError, type EventView } from "../api";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useConnectionStore } from "../store/connection";
import { CLOSE_UNAUTHENTICATED, openChannel, SOCKET_OPEN, type Channel, type SocketLike } from "./channel";
import type { Envelope } from "./envelopes";
import { watchPage, type PageLike } from "./pageVisibility";
import { backoffDelay, DEGRADED_POLL_INTERVAL_MS, HIDDEN_PAUSE_MS, PING_INTERVAL_MS, STABLE_OPEN_MS } from "./timeouts";

class FakeSocket implements SocketLike {
  readyState = 0;
  sent: string[] = [];
  onopen: SocketLike["onopen"] = null;
  onmessage: SocketLike["onmessage"] = null;
  onclose: SocketLike["onclose"] = null;
  onerror: SocketLike["onerror"] = null;

  /** The server accepted. */
  accept() {
    this.readyState = SOCKET_OPEN;
    this.onopen?.({} as Event);
  }
  /** The server sent a frame. */
  receive(frame: object) {
    this.onmessage?.({ data: JSON.stringify(frame) } as MessageEvent);
  }
  /** The server closed. */
  drop(code = 1006) {
    this.readyState = 3;
    this.onclose?.({ code } as CloseEvent);
  }
  send(data: string) {
    this.sent.push(data);
  }
  close() {
    if (this.readyState === 3) return;
    this.drop();
  }
}

function event(seq: number): EventView {
  return { seq, kind: "tasks.task.updated", target_id: `t${seq}`, produced_at: "2026-09-16T12:00:00Z", actor_id: "u1" };
}

function push(seq: number): Envelope {
  return { type: "event", topic: "entity_changed", sent_at: null, payload: { kind: "tasks.task.updated", target_id: `t${seq}`, seq, actor_id: "u1" } };
}

const seqOf = (e: Envelope) => (e as { payload: { seq: number } }).payload.seq;

const REMINDED = "tasks.task.reminded";

/** A reminder of task `target` in the stream. */
function reminded(seq: number, target: string): EventView {
  return { ...event(seq), kind: REMINDED, target_id: target };
}

const isReminder = (e: Envelope) => e.type === "event" && e.payload.kind === REMINDED;

const hello = (seq: number) => ({ type: "hello", sent_at: null, org_id: "o1", user_id: "u1", seq, ping_interval_seconds: 25 });

/** The stream in storage, as pages after a seq. */
type Pages = (after: number) => EventView[];

function harness(pages: Pages = () => [], pageSize = 200, clock?: () => number, split = false) {
  const sockets: FakeSocket[] = [];
  const routed: Envelope[] = [];
  const replayed: Envelope[] = [];
  const announced: number[][] = [];
  const fetches: number[] = [];
  const requestTicket = vi.fn(() => Promise.resolve("tkt"));
  const onUnauthenticated = vi.fn();
  const refreshAll = vi.fn(() => Promise.resolve());
  const channel = openChannel({
    now: clock,
    onUnauthenticated,
    requestTicket,
    openSocket: () => {
      const socket = new FakeSocket();
      sockets.push(socket);
      return socket;
    },
    fetchEventsAfter: (after) => {
      fetches.push(after);
      return Promise.resolve(pages(after));
    },
    route: (envelope) => {
      routed.push(envelope);
    },
    routeReplayed: split ? (envelope) => void replayed.push(envelope) : undefined,
    isAnnounced: isReminder,
    announce: (envelopes) => void announced.push(envelopes.map(seqOf)),
    refreshAll,
    connection: useConnectionStore,
    pageSize,
  });
  return { channel, sockets, routed, replayed, announced, fetches, requestTicket, onUnauthenticated, refreshAll };
}

// Lets the ticket request and the inbox settle without moving the clock.
const flush = () => vi.advanceTimersByTimeAsync(0);

let channel: Channel | null = null;

beforeEach(() => {
  vi.useFakeTimers();
  useConnectionStore.setState({ status: "closed", failedCycles: 0 });
});

afterEach(() => {
  channel?.stop();
  channel = null;
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("reconnect backoff", () => {
  // Each wait is drawn from a window, and the windows of two attempts meet:
  // the second one opens where the first one closes. A check timed on that
  // edge passes or fails by the draw. So a test that times a reconnect runs
  // at both ends of the jitter, with Math.random fixed, and waits exactly the
  // delay drawn. 1 - 2 ** -53 is the largest double below 1, the top of what
  // Math.random may answer.
  const JITTER_ENDS = [
    { end: "shortest", draw: 0 },
    { end: "longest", draw: 1 - 2 ** -53 },
  ];

  /** Fixes the jitter at `draw` for this test; answers the wait of each attempt. */
  function jitterAt(draw: number): (attempt: number) => number {
    vi.spyOn(Math, "random").mockReturnValue(draw);
    return (attempt) => backoffDelay(attempt, () => draw);
  }

  it("signs out on a 4401 close and never reconnects", async () => {
    const h = harness();
    channel = h.channel;
    await flush();
    h.sockets[0]!.accept();
    h.sockets[0]!.drop(CLOSE_UNAUTHENTICATED);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(h.onUnauthenticated).toHaveBeenCalledTimes(1);
    expect(h.requestTicket).toHaveBeenCalledTimes(1);
  });

  it("signs out on a 4401 close of an open channel too, as at the session's expiry", async () => {
    // The server closes a socket that said hello when the session behind it
    // expires or is revoked; the portal signs out rather than reconnecting.
    const h = harness();
    channel = h.channel;
    await flush();
    h.sockets[0]!.accept();
    h.sockets[0]!.receive(hello(3));
    await flush();
    h.sockets[0]!.drop(CLOSE_UNAUTHENTICATED);
    expect(useConnectionStore.getState().status).toBe("closed");
    await vi.advanceTimersByTimeAsync(60_000);
    expect(h.onUnauthenticated).toHaveBeenCalledTimes(1);
    expect(h.requestTicket).toHaveBeenCalledTimes(1);
    expect(h.sockets).toHaveLength(1);
  });

  it.each(JITTER_ENDS)("keeps backing off while the server accepts and closes at once, each wait at its $end", async ({ draw }) => {
    const wait = jitterAt(draw);
    const h = harness();
    channel = h.channel;
    await flush();
    h.sockets[0]!.accept();
    h.sockets[0]!.drop();
    expect(h.requestTicket).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(wait(1));
    expect(h.requestTicket).toHaveBeenCalledTimes(2);
    h.sockets[1]!.accept();
    h.sockets[1]!.drop();

    // The second try waits twice as long: an accept without a hello is not a connection.
    await vi.advanceTimersByTimeAsync(wait(2) - 1);
    expect(h.requestTicket).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(1);
    expect(h.requestTicket).toHaveBeenCalledTimes(3);
    expect(useConnectionStore.getState().status).not.toBe("open");
  });

  it.each(JITTER_ENDS)("starts the backoff over once the hello frame has arrived, each wait at its $end", async ({ draw }) => {
    const wait = jitterAt(draw);
    const h = harness();
    channel = h.channel;
    await flush();
    h.sockets[0]!.accept();
    h.sockets[0]!.drop();
    await vi.advanceTimersByTimeAsync(wait(1));
    h.sockets[1]!.accept();
    expect(useConnectionStore.getState().status).toBe("connecting");
    h.sockets[1]!.receive(hello(5));
    expect(useConnectionStore.getState().status).toBe("open");
    h.sockets[1]!.drop();

    // Back to the first delay, which is half the second one.
    await vi.advanceTimersByTimeAsync(wait(1));
    expect(h.requestTicket).toHaveBeenCalledTimes(3);
  });

  it("says connecting the moment an open socket drops, through the backoff and the ticket request", async () => {
    // The indicator never says live without a socket: a drop is a status
    // change on its own, not something the next successful connect fixes.
    const h = harness();
    channel = h.channel;
    await flush();
    h.sockets[0]!.accept();
    h.sockets[0]!.receive(hello(5));
    expect(useConnectionStore.getState().status).toBe("open");
    h.requestTicket.mockImplementation(() => new Promise(() => undefined));
    h.sockets[0]!.drop();
    expect(useConnectionStore.getState().status).toBe("connecting");
    await vi.advanceTimersByTimeAsync(1_000);
    expect(h.requestTicket).toHaveBeenCalledTimes(2);
    expect(useConnectionStore.getState().status).toBe("connecting");
  });

  it("says degraded from the second failed cycle on, through the ticket request too", async () => {
    const h = harness();
    channel = h.channel;
    await flush();
    h.sockets[0]!.accept();
    h.sockets[0]!.drop();
    await vi.advanceTimersByTimeAsync(1_000);
    h.sockets[1]!.accept();
    h.sockets[1]!.drop();
    expect(useConnectionStore.getState().status).toBe("degraded");
    await vi.advanceTimersByTimeAsync(2_000);
    expect(h.requestTicket).toHaveBeenCalledTimes(3);
    expect(useConnectionStore.getState().status).toBe("degraded");
  });

  it("leaves no failed cycles behind, so the next session starts undegraded", async () => {
    // Signing out mid-reconnect ends this channel; the count belonged to it.
    // Left standing, the next session's first drop was already the second
    // failed cycle: the degraded banner for a channel that has dropped once.
    const first = harness();
    first.sockets[0]?.accept();
    await flush();
    first.sockets[0]!.drop();
    await flush();
    expect(useConnectionStore.getState().failedCycles).toBe(1);
    first.channel.stop();
    expect(useConnectionStore.getState().failedCycles).toBe(0);

    const second = harness();
    channel = second.channel;
    await flush();
    second.sockets[0]!.accept();
    second.sockets[0]!.drop();
    expect(useConnectionStore.getState().status).toBe("connecting");
  });

  it.each(JITTER_ENDS)("counts a socket that stays open long enough as connected even without a hello, each wait at its $end", async ({ draw }) => {
    const wait = jitterAt(draw);
    const h = harness();
    channel = h.channel;
    await flush();
    h.sockets[0]!.accept();
    h.sockets[0]!.drop();
    await vi.advanceTimersByTimeAsync(wait(1));
    h.sockets[1]!.accept();
    await vi.advanceTimersByTimeAsync(STABLE_OPEN_MS);
    expect(useConnectionStore.getState().status).toBe("open");
    h.sockets[1]!.drop();
    // Back to the first delay.
    await vi.advanceTimersByTimeAsync(wait(1));
    expect(h.requestTicket).toHaveBeenCalledTimes(3);
  });

  it("subscribes on open and stops for good when told to", async () => {
    const h = harness();
    channel = h.channel;
    await flush();
    h.sockets[0]!.accept();
    expect(h.sockets[0]!.sent).toEqual([JSON.stringify({ op: "subscribe", topic: "entity_changed" })]);
    channel.stop();
    channel = null;
    expect(h.sockets[0]!.readyState).toBe(3);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(h.requestTicket).toHaveBeenCalledTimes(1);
    expect(useConnectionStore.getState().status).toBe("closed");
  });
});

describe("stream cursor", () => {
  it("takes the cursor from the hello and advances on contiguous pushes", async () => {
    const h = harness();
    channel = h.channel;
    await flush();
    h.sockets[0]!.accept();
    await flush();
    h.sockets[0]!.receive(hello(5));
    h.sockets[0]!.receive(push(6));
    h.sockets[0]!.receive(push(7));
    await flush();
    expect(channel.cursor()).toBe(7);
    expect(h.routed.filter((e) => e.type === "event")).toHaveLength(2);
    expect(h.fetches).toEqual([]);
  });

  it("closes a gap by replaying the pages between the cursor and the push", async () => {
    const stream = [event(6), event(7), event(8)];
    const h = harness((after) => stream.filter((e) => e.seq > after).slice(0, 2), 2);
    channel = h.channel;
    await flush();
    h.sockets[0]!.accept();
    await flush();
    h.sockets[0]!.receive(hello(5));
    await flush();
    h.sockets[0]!.receive(push(8));
    await flush();
    expect(h.fetches).toEqual([5, 7]);
    expect(channel.cursor()).toBe(8);
    // One route per entity per page, the last record of it: routing a record
    // invalidates every query the entity is read from, so routing all three
    // would refetch the same lists three times over.
    expect(h.routed.filter((e) => e.type === "event").map(seqOf)).toEqual([7, 8]);
  });

  it("routes a live push as live, and a record read back from the stream as replayed", async () => {
    // A live task push reads that one task; a replay routes only the last
    // record of each entity, so the lists it touches are read whole.
    const stream = [event(6), event(7), event(8)];
    const h = harness((after) => stream.filter((e) => e.seq > after), 200, undefined, true);
    channel = h.channel;
    await flush();
    h.sockets[0]!.accept();
    await flush();
    h.sockets[0]!.receive(hello(5));
    await flush();
    h.sockets[0]!.receive(push(6));
    await flush();
    h.sockets[0]!.receive(push(8));
    await flush();
    // Push 8 found 7 missing: the replay of 7 and 8 routed the last of them.
    expect(h.routed.filter((e) => e.type === "event").map(seqOf)).toEqual([6]);
    expect(h.replayed.map(seqOf)).toEqual([8]);
    expect(channel.cursor()).toBe(8);
  });

  it("routes a replay page once per entity, however many records it carries", async () => {
    // A tab that wakes far behind replays a full page at a time. Each record
    // routed cancels and restarts the refetch of every task list, so a page of
    // task records used to cost one list request per record.
    const stream: EventView[] = [];
    for (let seq = 6; seq <= 25; seq += 1) {
      stream.push({ ...event(seq), kind: seq % 2 === 0 ? "tasks.task.updated" : "tenancy.user.deleted" });
    }
    const h = harness((after) => stream.filter((e) => e.seq > after).slice(0, 20), 20);
    channel = h.channel;
    await flush();
    h.sockets[0]!.accept();
    await flush();
    h.sockets[0]!.receive(hello(5));
    await flush();
    h.sockets[0]!.receive(push(25));
    await flush();
    expect(channel.cursor()).toBe(25);
    const kinds = h.routed
      .filter((e) => e.type === "event")
      .map((e) => (e as { payload: { kind: string } }).payload.kind);
    expect(kinds).toEqual(["tasks.task.updated", "tenancy.user.deleted"]);
  });

  it("stops paging when a page moves the cursor nowhere", async () => {
    // Seq 6 is not in storage yet; every page after 5 starts at 7 and is full.
    const stream = [event(7), event(8), event(9)];
    let calls = 0;
    const h = harness((after) => {
      calls += 1;
      if (calls > 3) throw new Error("hammered");
      return stream.filter((e) => e.seq > after).slice(0, 2);
    }, 2);
    channel = h.channel;
    await flush();
    h.sockets[0]!.accept();
    await flush();
    h.sockets[0]!.receive(hello(5));
    await flush();
    h.sockets[0]!.receive(push(9));
    await flush();
    expect(h.fetches).toEqual([5]);
    expect(channel.cursor()).toBe(5);
    // The push is still worth routing: the entity did change.
    expect(h.routed.filter((e) => e.type === "event").map(seqOf)).toEqual([9]);
  });

  it("never moves the cursor past seqs that were not replayed", async () => {
    // Storage answers with a short page that skips 6: the replay ends, the gap stays.
    const h = harness((after) => [event(7), event(8)].filter((e) => e.seq > after));
    channel = h.channel;
    await flush();
    h.sockets[0]!.accept();
    await flush();
    h.sockets[0]!.receive(hello(5));
    await flush();
    h.sockets[0]!.receive(push(8));
    await flush();
    expect(h.routed.filter((e) => e.type === "event").map(seqOf)).toEqual([8]);
    expect(channel.cursor()).toBe(5);

    // Once 6 exists, the next push replays through it and the cursor catches up.
    const h2 = h;
    h2.sockets[0]!.receive(push(6));
    await flush();
    expect(channel.cursor()).toBe(6);
  });
});

describe("a reminder read back from the stream", () => {
  /** A channel with its cursor at 5, then dropped and reconnected: the open
   * replays from the cursor. */
  async function reconnected(stream: EventView[], pageSize = 200) {
    const h = harness((after) => stream.filter((e) => e.seq > after).slice(0, pageSize), pageSize, undefined, true);
    channel = h.channel;
    await flush();
    h.sockets[0]!.accept();
    await flush();
    h.sockets[0]!.receive(hello(5));
    await flush();
    h.sockets[0]!.drop();
    await vi.advanceTimersByTimeAsync(1_000);
    h.sockets[1]!.accept();
    await flush();
    return h;
  }

  it("is kept through the collapse: a later update of the same task does not hide it", async () => {
    const h = await reconnected([reminded(6, "t1"), { ...event(7), target_id: "t1" }, event(8)]);
    expect(channel!.cursor()).toBe(8);
    // The lists are read once, from the last task record; the reminder is announced.
    expect(h.replayed.map(seqOf)).toEqual([8]);
    expect(h.announced).toEqual([[6]]);
  });

  it("hands over every reminder of a replay at once, across its pages, in stream order", async () => {
    const stream = [reminded(6, "t1"), event(7), reminded(8, "t2"), reminded(9, "t3"), event(10)];
    const h = await reconnected(stream, 2);
    expect(h.fetches).toEqual([5, 7, 9]);
    expect(channel!.cursor()).toBe(10);
    expect(h.announced).toEqual([[6, 8, 9]]);
  });

  it("announces nothing for a replay with no reminder", async () => {
    const h = await reconnected([event(6), event(7)]);
    expect(channel!.cursor()).toBe(7);
    expect(h.announced).toEqual([]);
  });

  it("is handed over when a later page fails, since the cursor has moved past it", async () => {
    let calls = 0;
    const stream = [reminded(6, "t1"), event(7), event(8), event(9)];
    const h = harness(
      (after) => {
        calls += 1;
        if (calls > 1) throw new Error("the network went away");
        return stream.filter((e) => e.seq > after).slice(0, 2);
      },
      2,
      undefined,
      true,
    );
    channel = h.channel;
    await flush();
    h.sockets[0]!.accept();
    await flush();
    h.sockets[0]!.receive(hello(5));
    await flush();
    h.sockets[0]!.drop();
    await vi.advanceTimersByTimeAsync(1_000);
    h.sockets[1]!.accept();
    await flush();
    expect(channel.cursor()).toBe(7);
    expect(h.announced).toEqual([[6]]);
  });

  it("is kept by the first catch-up too", async () => {
    let clock = 0;
    const stream = [
      { ...reminded(4, "t1"), produced_at: "2026-09-16T12:00:01Z" },
      { ...event(5), target_id: "t1", produced_at: "2026-09-16T12:00:02Z" },
    ];
    const h = harness((after) => stream.filter((e) => e.seq > after), 200, () => clock, true);
    channel = h.channel;
    await flush();
    h.sockets[0]!.accept();
    await flush();
    clock += 3000;
    h.sockets[0]!.receive({ ...hello(5), sent_at: "2026-09-16T12:00:03Z" });
    await flush();
    expect(h.replayed.map(seqOf)).toEqual([5]);
    expect(h.announced).toEqual([[4]]);
  });

  it("is not handed over by a replay that finishes after stop", async () => {
    let resolvePage!: (page: EventView[]) => void;
    const socket = new FakeSocket();
    const announce = vi.fn();
    channel = openChannel({
      requestTicket: async () => "tkt",
      openSocket: () => socket,
      fetchEventsAfter: vi.fn(() => new Promise<EventView[]>((resolve) => { resolvePage = resolve; })),
      route: vi.fn(),
      isAnnounced: isReminder,
      announce,
      refreshAll: async () => undefined,
      connection: useConnectionStore,
      pageSize: 200,
    });
    await flush();
    socket.accept();
    await flush();
    socket.receive(hello(0));
    await flush();
    socket.receive(push(2));
    await flush();
    channel.stop();
    resolvePage([reminded(1, "t1"), event(2)]);
    await flush();
    expect(announce).not.toHaveBeenCalled();
  });
});

describe("a stream trimmed past the cursor", () => {
  // The trim took everything up to 7; the stream holds 8 and 9. A read after
  // a seq below 7 is refused as the API refuses it, naming the head.
  const trimmed = (stream: EventView[], floor = 7) => (after: number) => {
    if (after < floor) {
      const head = stream[stream.length - 1]!.seq;
      throw new ApiError(410, "stream_truncated", "gone", null, undefined, null, { floor, head });
    }
    return stream.filter((e) => e.seq > after);
  };

  it("reads everything afresh once and goes on from the head", async () => {
    const h = harness(trimmed([event(8), event(9)]));
    channel = h.channel;
    await flush();
    h.sockets[0]!.accept();
    await flush();
    h.sockets[0]!.receive(hello(5));
    await flush();
    // The hello carries no time, so the first catch-up refreshed once already.
    h.refreshAll.mockClear();
    h.sockets[0]!.receive(push(9));
    await flush();
    expect(h.fetches).toEqual([5]);
    expect(h.refreshAll).toHaveBeenCalledTimes(1);
    expect(channel.cursor()).toBe(9);

    // No loop: the pong at the head and the next push read nothing again.
    h.sockets[0]!.receive({ type: "pong", sent_at: null, seq: 9 });
    h.sockets[0]!.receive(push(10));
    await flush();
    expect(h.fetches).toEqual([5]);
    expect(h.refreshAll).toHaveBeenCalledTimes(1);
    expect(channel.cursor()).toBe(10);
    expect(h.routed.filter((e) => e.type === "event").map(seqOf)).toEqual([10]);
  });

  it("resyncs from a pong too, when no push announced the trim", async () => {
    const h = harness(trimmed([event(8), event(9)]));
    channel = h.channel;
    await flush();
    h.sockets[0]!.accept();
    await flush();
    h.sockets[0]!.receive(hello(5));
    await flush();
    // The hello carries no time, so the first catch-up refreshed once already.
    h.refreshAll.mockClear();
    h.sockets[0]!.receive({ type: "pong", sent_at: null, seq: 9 });
    h.sockets[0]!.receive({ type: "pong", sent_at: null, seq: 9 });
    await flush();
    expect(h.fetches).toEqual([5]);
    expect(h.refreshAll).toHaveBeenCalledTimes(1);
    expect(channel.cursor()).toBe(9);
  });

  it("resyncs on the reconnect of a tab that slept past the trim", async () => {
    const h = harness(trimmed([event(8), event(9)]));
    channel = h.channel;
    await flush();
    h.sockets[0]!.accept();
    await flush();
    h.sockets[0]!.receive(hello(5));
    await flush();
    // The hello carries no time, so the first catch-up refreshed once already.
    h.refreshAll.mockClear();
    h.sockets[0]!.drop();
    await vi.advanceTimersByTimeAsync(1_000);
    h.sockets[1]!.accept();
    await flush();
    expect(h.fetches).toEqual([5]);
    expect(h.refreshAll).toHaveBeenCalledTimes(1);
    expect(channel.cursor()).toBe(9);
  });

  it("keeps the cursor on any other failure, and replays from it next time", async () => {
    let down = true;
    const stream = [event(6), event(7)];
    const h = harness((after) => {
      if (down) throw new ApiError(503, "unavailable", "later", null);
      return stream.filter((e) => e.seq > after);
    });
    channel = h.channel;
    await flush();
    h.sockets[0]!.accept();
    await flush();
    h.sockets[0]!.receive(hello(5));
    await flush();
    // The hello carries no time, so the first catch-up refreshed once already.
    h.refreshAll.mockClear();
    h.sockets[0]!.receive(push(7));
    await flush();
    expect(h.refreshAll).not.toHaveBeenCalled();
    expect(channel.cursor()).toBe(5);
    down = false;
    h.sockets[0]!.receive({ type: "pong", sent_at: null, seq: 7 });
    await flush();
    expect(channel.cursor()).toBe(7);
  });
});

describe("the first catch-up", () => {
  // The page began reading at 12:00:00 on the server's clock: the hello was
  // sent at 12:00:03, three seconds after the channel opened.
  const helloAt = (seq: number) => ({ ...hello(seq), sent_at: "2026-09-16T12:00:03Z" });
  const at = (seq: number, time: string, kind = "tasks.task.updated"): EventView => ({
    ...event(seq),
    kind,
    produced_at: `2026-09-16T${time}Z`,
  });

  function opened(pages: Pages, pageSize = 200) {
    let clock = 0;
    const h = harness(pages, pageSize, () => clock);
    return { h, wait: (ms: number) => (clock += ms) };
  }

  it("routes what the stream produced since the page began reading, and refreshes nothing else", async () => {
    // 11:59:30 is past the margin before the reads; 11:59:50 is inside it,
    // and 12:00:01 came while the page read. One route per entity.
    const stream = [
      at(3, "11:59:30", "billing.account.updated"),
      at(4, "11:59:50", "tasks.task.created"),
      at(5, "12:00:01", "tasks.task.updated"),
    ];
    const { h, wait } = opened((after) => stream.filter((e) => e.seq > after));
    channel = h.channel;
    await flush();
    h.sockets[0]!.accept();
    await flush();
    wait(3000);
    h.sockets[0]!.receive(helloAt(5));
    await flush();
    expect(h.refreshAll).not.toHaveBeenCalled();
    expect(h.fetches).toEqual([0]);
    expect(h.routed.filter((e) => e.type === "event").map(seqOf)).toEqual([5]);
    expect(channel.cursor()).toBe(5);
  });

  it("reads only the last page of the stream", async () => {
    const stream = [at(400, "11:00:00"), at(401, "12:00:02")];
    const { h, wait } = opened((after) => stream.filter((e) => e.seq > after), 2);
    channel = h.channel;
    await flush();
    h.sockets[0]!.accept();
    await flush();
    wait(3000);
    h.sockets[0]!.receive(helloAt(401));
    await flush();
    expect(h.fetches).toEqual([399]);
    expect(h.routed.filter((e) => e.type === "event").map(seqOf)).toEqual([401]);
    expect(h.refreshAll).not.toHaveBeenCalled();
  });

  it("refreshes wholesale when the whole tail is recent, since older records may lie before it", async () => {
    const stream = [at(400, "12:00:01"), at(401, "12:00:02")];
    const { h, wait } = opened((after) => stream.filter((e) => e.seq > after), 2);
    channel = h.channel;
    await flush();
    h.sockets[0]!.accept();
    await flush();
    wait(3000);
    h.sockets[0]!.receive(helloAt(401));
    await flush();
    expect(h.refreshAll).toHaveBeenCalledTimes(1);
    expect(h.routed.filter((e) => e.type === "event")).toEqual([]);
  });

  it("reads nothing for an empty stream", async () => {
    const { h } = opened(() => []);
    channel = h.channel;
    await flush();
    h.sockets[0]!.accept();
    await flush();
    h.sockets[0]!.receive(helloAt(0));
    await flush();
    expect(h.fetches).toEqual([]);
    expect(h.refreshAll).not.toHaveBeenCalled();
  });

  it("refreshes wholesale when the hello carries no time", async () => {
    const h = harness();
    channel = h.channel;
    await flush();
    h.sockets[0]!.accept();
    await flush();
    h.sockets[0]!.receive(hello(5));
    await flush();
    expect(h.refreshAll).toHaveBeenCalledTimes(1);
    expect(h.fetches).toEqual([]);
  });

  it("refreshes wholesale when the socket stays open without a hello", async () => {
    const h = harness();
    channel = h.channel;
    await flush();
    h.sockets[0]!.accept();
    await flush();
    expect(h.refreshAll).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(STABLE_OPEN_MS);
    expect(h.refreshAll).toHaveBeenCalledTimes(1);
  });

  it("replays from the cursor on a reconnect, never from the tail", async () => {
    const stream = [at(6, "12:00:04")];
    const { h, wait } = opened((after) => stream.filter((e) => e.seq > after));
    channel = h.channel;
    await flush();
    h.sockets[0]!.accept();
    await flush();
    wait(3000);
    h.sockets[0]!.receive(helloAt(5));
    await flush();
    h.sockets[0]!.drop();
    await vi.advanceTimersByTimeAsync(60_000);
    h.sockets[1]!.accept();
    await flush();
    expect(h.fetches).toEqual([0, 5]);
    expect(h.refreshAll).not.toHaveBeenCalled();
  });
});

it("ignores an old session's late authentication close after a new session opens", async () => {
  const first = harness();
  await flush();
  first.sockets[0]!.accept();
  // Browser close is asynchronous; its final close event may arrive after cleanup.
  first.sockets[0]!.close = vi.fn();
  first.channel.stop();
  const second = harness();
  channel = second.channel;
  await flush();
  second.sockets[0]!.accept();
  second.sockets[0]!.receive(hello(0));
  first.sockets[0]!.drop(CLOSE_UNAUTHENTICATED);
  expect(first.onUnauthenticated).not.toHaveBeenCalled();
  expect(useConnectionStore.getState().status).toBe("open");
});

it("does not apply or continue a replay that finishes after stop", async () => {
  let resolvePage!: (page: EventView[]) => void;
  const socket = new FakeSocket();
  const route = vi.fn();
  const fetchEventsAfter = vi.fn(() => new Promise<EventView[]>((resolve) => { resolvePage = resolve; }));
  channel = openChannel({
    requestTicket: async () => "tkt", openSocket: () => socket,
    fetchEventsAfter, route, refreshAll: async () => undefined,
    connection: useConnectionStore, pageSize: 1,
  });
  await flush();
  socket.accept();
  await flush();
  socket.receive(hello(0));
  await flush();
  route.mockClear();
  socket.receive(push(2));
  await flush();
  channel.stop();
  resolvePage([event(1)]);
  await flush();
  expect(route).not.toHaveBeenCalled();
  expect(fetchEventsAfter).toHaveBeenCalledTimes(1);
  expect(channel.cursor()).toBe(0);
});

/** A page whose visibility the test sets: a document and a window with no DOM. */
function fakePage() {
  const document = Object.assign(new EventTarget(), { hidden: false });
  const window = new EventTarget();
  const page: PageLike = { document, window };
  return {
    page,
    hide() {
      document.hidden = true;
      document.dispatchEvent(new Event("visibilitychange"));
    },
    show() {
      document.hidden = false;
      document.dispatchEvent(new Event("visibilitychange"));
    },
    /** A window event; the page is visible unless `hidden` says otherwise. */
    fire(type: string, fields: object = {}, hidden = false) {
      document.hidden = hidden;
      window.dispatchEvent(Object.assign(new Event(type), fields));
    },
  };
}

describe("a hidden tab", () => {
  // The stream after the hello's 5: two changes land while the tab is away.
  const stream = [event(6), event(7)];
  const pages = (after: number) => stream.filter((e) => e.seq > after);
  const status = () => useConnectionStore.getState().status;

  /** An open channel at cursor 5, watching a fake page. */
  async function watched(p: Pages = pages) {
    const h = harness(p);
    channel = h.channel;
    const page = fakePage();
    const unwatch = watchPage(page.page, h.channel);
    await flush();
    h.sockets[0]!.accept();
    h.sockets[0]!.receive(hello(5));
    await flush();
    expect(status()).toBe("open");
    return { h, page, unwatch };
  }

  it("pauses after five minutes hidden: no socket, no reconnect, no polling, no failure", async () => {
    const { h, page } = await watched();
    page.hide();
    await vi.advanceTimersByTimeAsync(HIDDEN_PAUSE_MS - 1);
    expect(h.sockets[0]!.readyState).toBe(SOCKET_OPEN);
    await vi.advanceTimersByTimeAsync(1);
    expect(h.sockets[0]!.readyState).toBe(3);
    expect(status()).toBe("paused");
    expect(useConnectionStore.getState().failedCycles).toBe(0);

    // Ten minutes more hidden: nothing is asked of the API, and nothing is sent.
    const sent = h.sockets[0]!.sent.length;
    await vi.advanceTimersByTimeAsync(10 * 60_000);
    expect(h.requestTicket).toHaveBeenCalledTimes(1);
    expect(h.sockets).toHaveLength(1);
    expect(h.fetches).toEqual([]);
    expect(h.sockets[0]!.sent).toHaveLength(sent);
    expect(status()).toBe("paused");
  });

  it("reconnects with a fresh ticket on return and catches up from the cursor", async () => {
    const { h, page } = await watched();
    page.hide();
    await vi.advanceTimersByTimeAsync(HIDDEN_PAUSE_MS + 60_000);
    expect(channel!.cursor()).toBe(5);

    page.show();
    await flush();
    expect(h.requestTicket).toHaveBeenCalledTimes(2);
    expect(status()).toBe("connecting");
    h.sockets[1]!.accept();
    await flush();
    expect(h.fetches).toEqual([5]);
    expect(channel!.cursor()).toBe(7);
    h.sockets[1]!.receive(hello(7));
    expect(status()).toBe("open");
  });

  it("resumes on a page restored from the back/forward cache", async () => {
    const { h, page } = await watched();
    page.hide();
    await vi.advanceTimersByTimeAsync(HIDDEN_PAUSE_MS);
    page.fire("pageshow", { persisted: true });
    await flush();
    h.sockets[1]!.accept();
    await flush();
    expect(h.requestTicket).toHaveBeenCalledTimes(2);
    expect(h.fetches).toEqual([5]);
    expect(channel!.cursor()).toBe(7);
  });

  it.each(["focus", "online"])("resumes on %s", async (type) => {
    const { h, page } = await watched();
    page.hide();
    await vi.advanceTimersByTimeAsync(HIDDEN_PAUSE_MS);
    page.fire(type);
    await flush();
    h.sockets[1]!.accept();
    await flush();
    expect(h.requestTicket).toHaveBeenCalledTimes(2);
    expect(h.fetches).toEqual([5]);
  });

  it("pauses again after an online that finds the tab still hidden", async () => {
    const { h, page } = await watched();
    page.hide();
    await vi.advanceTimersByTimeAsync(HIDDEN_PAUSE_MS);
    page.fire("online", {}, true);
    await flush();
    h.sockets[1]!.accept();
    await flush();
    expect(h.fetches).toEqual([5]);
    await vi.advanceTimersByTimeAsync(HIDDEN_PAUSE_MS);
    expect(h.sockets[1]!.readyState).toBe(3);
    expect(status()).toBe("paused");
  });

  it("never pauses on a hide shorter than five minutes", async () => {
    const { h, page } = await watched();
    for (let i = 0; i < 3; i += 1) {
      page.hide();
      await vi.advanceTimersByTimeAsync(HIDDEN_PAUSE_MS - 1_000);
      page.show();
      await vi.advanceTimersByTimeAsync(1_000);
    }
    expect(h.sockets).toHaveLength(1);
    expect(h.sockets[0]!.readyState).toBe(SOCKET_OPEN);
    expect(h.requestTicket).toHaveBeenCalledTimes(1);
    expect(status()).toBe("open");
  });

  it("makes one connect of many return signals at once", async () => {
    const { h, page } = await watched();
    page.hide();
    await vi.advanceTimersByTimeAsync(HIDDEN_PAUSE_MS);
    page.show();
    page.fire("pageshow", { persisted: true });
    page.fire("focus");
    page.fire("online");
    page.show();
    await flush();
    expect(h.requestTicket).toHaveBeenCalledTimes(2);
    expect(h.sockets).toHaveLength(2);
    h.sockets[1]!.accept();
    await flush();
    page.fire("focus");
    await flush();
    expect(h.requestTicket).toHaveBeenCalledTimes(2);
    expect(h.fetches).toEqual([5]);
  });

  it("stops a degraded channel's reconnects and polling at the pause, and starts clean on return", async () => {
    const h = harness(pages);
    channel = h.channel;
    const page = fakePage();
    watchPage(page.page, h.channel);
    await flush();
    h.sockets[0]!.accept();
    h.sockets[0]!.drop();
    await vi.advanceTimersByTimeAsync(1_000);
    h.sockets[1]!.accept();
    h.sockets[1]!.drop();
    expect(status()).toBe("degraded");

    page.hide();
    await vi.advanceTimersByTimeAsync(HIDDEN_PAUSE_MS);
    expect(status()).toBe("paused");
    const tickets = h.requestTicket.mock.calls.length;
    const reads = h.refreshAll.mock.calls.length;
    await vi.advanceTimersByTimeAsync(10 * DEGRADED_POLL_INTERVAL_MS);
    expect(h.requestTicket).toHaveBeenCalledTimes(tickets);
    expect(h.refreshAll).toHaveBeenCalledTimes(reads);

    // The return is a first connect: its first drop is a reconnect, not degraded.
    page.show();
    await flush();
    const returned = h.sockets[h.sockets.length - 1]!;
    returned.accept();
    returned.drop();
    expect(status()).toBe("connecting");
  });

  it("opens nothing from a ticket request the pause overtook", async () => {
    const h = harness(pages);
    channel = h.channel;
    const page = fakePage();
    watchPage(page.page, h.channel);
    let answer!: (ticket: string) => void;
    h.requestTicket.mockImplementationOnce(() => new Promise((resolve) => { answer = resolve; }));
    await flush();
    // The first request is already out; the next one hangs until answered.
    h.sockets[0]!.accept();
    h.sockets[0]!.drop();
    await vi.advanceTimersByTimeAsync(1_000);
    page.hide();
    await vi.advanceTimersByTimeAsync(HIDDEN_PAUSE_MS);
    answer("late");
    await flush();
    expect(h.sockets).toHaveLength(1);
    expect(status()).toBe("paused");
  });

  it("re-reads everything on return when the stream was trimmed past the cursor", async () => {
    const trimmed = (after: number) => {
      if (after < 7) throw new ApiError(410, "stream_truncated", "gone", null, undefined, null, { floor: 7, head: 9 });
      return [];
    };
    const { h, page } = await watched(trimmed);
    h.refreshAll.mockClear();
    page.hide();
    await vi.advanceTimersByTimeAsync(HIDDEN_PAUSE_MS);
    page.show();
    await flush();
    h.sockets[1]!.accept();
    await flush();
    expect(h.fetches).toEqual([5]);
    expect(h.refreshAll).toHaveBeenCalledTimes(1);
    expect(channel!.cursor()).toBe(9);
  });

  it("asks for a ticket on return, whose 401 is the sign-out, and connects nothing more", async () => {
    // The transport client signs out on a 401 to the bearer the tab holds;
    // the sign-out ends the session, and the provider stops the channel.
    const { h, page } = await watched();
    page.hide();
    await vi.advanceTimersByTimeAsync(HIDDEN_PAUSE_MS);
    const signOut = vi.fn(() => channel!.stop());
    h.requestTicket.mockImplementationOnce(() => {
      signOut();
      return Promise.reject(new ApiError(401, "unauthenticated", "expired", null, undefined, null, null));
    });
    page.show();
    await flush();
    expect(signOut).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(h.requestTicket).toHaveBeenCalledTimes(2);
    expect(h.sockets).toHaveLength(1);
    expect(status()).toBe("closed");
  });

  it("stops listening to the page when unwatched", async () => {
    const { h, page, unwatch } = await watched();
    unwatch();
    page.hide();
    await vi.advanceTimersByTimeAsync(HIDDEN_PAUSE_MS + PING_INTERVAL_MS);
    expect(h.sockets[0]!.readyState).toBe(SOCKET_OPEN);
    expect(status()).toBe("open");
  });
});
