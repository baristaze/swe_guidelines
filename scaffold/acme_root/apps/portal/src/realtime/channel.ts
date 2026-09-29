// The socket's whole life without React: ticket, connect, subscribe, ping,
// reconnect with backoff, the degraded polling mode, the pause of a hidden
// tab, and the stream cursor.
// Everything it reaches for is handed in, so the loop runs in a test over a
// fake socket and fake timers. The provider owns one of these per session.
import type { EventView } from "../api";
import type { ConnectionState } from "../store/connection";
import { entityOf, isEntityChanged, parseEnvelope, type ClientCommand, type Envelope } from "./envelopes";
import {
  behind,
  eventEnvelope,
  isLastPage,
  place,
  readsBegan,
  tailSince,
  truncatedHead,
  type Cursor,
} from "./stream";
import { backoffDelay, DEGRADED_POLL_INTERVAL_MS, HIDDEN_PAUSE_MS, PING_INTERVAL_MS, STABLE_OPEN_MS } from "./timeouts";

const TOPICS = ["entity_changed"];

/** WebSocket.OPEN, named here so the loop needs no global to compare against. */
export const SOCKET_OPEN = 1;

/** What the loop asks of a socket; a browser WebSocket satisfies it. */
export interface SocketLike {
  readyState: number;
  send(data: string): void;
  close(): void;
  onopen: ((event: Event) => void) | null;
  onmessage: ((event: MessageEvent) => void) | null;
  onclose: ((event: CloseEvent) => void) | null;
  onerror: ((event: Event) => void) | null;
}

export interface ChannelDeps {
  requestTicket(): Promise<string>;
  openSocket(ticket: string): SocketLike;
  fetchEventsAfter(after: number, limit: number): Promise<EventView[]>;
  /** Hands one envelope to the router; the query cache is behind it. */
  route(envelope: Envelope): void;
  /** Hands the router a record read back from the stream (a replay, the
   * first catch-up). Only the last record of each entity is routed, so the
   * entity's queries are read again whole; `route` when absent. */
  routeReplayed?(envelope: Envelope): void;
  /** Whether a record read back from the stream is one a person is told
   * about, not only refreshed by: a reminder. The collapse to one record per
   * entity keeps each such record, whatever record of its entity follows. */
  isAnnounced?(envelope: Envelope): boolean;
  /** The records a read-back kept for `isAnnounced`, in stream order, handed
   * over once it ends: one call for a whole replay, every page of it, and one
   * for the first catch-up. Only what the read-back read is ever handed. */
  announce?(envelopes: Envelope[]): void;
  /** Refreshes every query, for a first catch-up the stream's tail cannot answer. */
  refreshAll(): Promise<unknown>;
  connection: { getState(): ConnectionState };
  /** Records per replay page; the production size is EVENTS_PAGE. */
  pageSize: number;
  /** The server closed with 4401: the session is gone; sign out instead of reconnecting. */
  onUnauthenticated?(): void;
  /** A monotonic clock in milliseconds; a test hands in its own. */
  now?(): number;
}

export const CLOSE_UNAUTHENTICATED = 4401;

export interface Channel {
  stop(): void;
  /** The tab was hidden: the socket closes if it stays hidden HIDDEN_PAUSE_MS. */
  hide(): void;
  /** The tab is back: a pending pause is called off, and a paused socket
   * reconnects and catches up from the cursor. Safe to call any number of
   * times; only a paused channel connects. */
  show(): void;
  /** The last contiguous seq applied; for tests and the degraded banner. */
  cursor(): Cursor;
}

export function openChannel(deps: ChannelDeps): Channel {
  // The store's actions, which never change; its state changes on every set,
  // so a read of it goes through deps.connection.getState() each time.
  const connection = deps.connection.getState();
  let socket: SocketLike | null = null;
  let pingTimer: ReturnType<typeof setInterval> | null = null;
  let pollTimer: ReturnType<typeof setInterval> | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let stableTimer: ReturnType<typeof setTimeout> | null = null;
  let pauseTimer: ReturnType<typeof setTimeout> | null = null;
  let attempt = 0;
  let stopped = false;
  let paused = false;
  // Moves on every pause, so a ticket request or a socket from before it
  // opens nothing after it.
  let generation = 0;
  let cursor: Cursor = null;
  const now = deps.now ?? (() => performance.now());
  // When the page began reading, near enough: the provider opens the channel
  // in the same render that mounts the page's first queries.
  const startedAt = now();
  const routeReplayed = deps.routeReplayed ?? deps.route;
  const isAnnounced = deps.isAnnounced ?? (() => false);
  // Hands over what a read-back kept, unless the channel stopped meanwhile:
  // a switch or a sign-out drops what the old session read.
  const announceKept = (kept: Envelope[]) => {
    if (!stopped && kept.length > 0) deps.announce?.(kept);
  };
  // Frames and replays are applied strictly in arrival order.
  let inbox: Promise<void> = Promise.resolve();

  const send = (command: ClientCommand) => {
    if (socket?.readyState === SOCKET_OPEN) socket.send(JSON.stringify(command));
  };

  // Routes one envelope unless it is behind the cursor; returns the seq to
  // replay after when the envelope is ahead of it. `route` is where the
  // envelope goes: the router by default, and a replay's collector when a
  // page is being coalesced.
  const apply = (envelope: Envelope, route: (routed: Envelope) => void = deps.route): number | null => {
    if (!isEntityChanged(envelope)) {
      route(envelope);
      return null;
    }
    const placement = place(cursor, envelope.payload.seq);
    if (placement.kind === "seen") return null;
    if (placement.kind === "gap") return placement.after;
    route(envelope);
    if (placement.kind === "next") cursor = placement.cursor;
    return null;
  };

  // The stream is trimmed past the cursor, so no replay can close the gap.
  // Every query is read afresh, and the cursor moves to the head the refusal
  // named: the next push or pong replays from there, once, and never asks
  // below the trim again.
  const resync = async (head: number) => {
    await deps.refreshAll();
    if (stopped) return;
    if (cursor === null || head > cursor) cursor = head;
  };

  // Pages through the stream after `after`, applying each record through
  // the router, until the last page, a failed fetch, or a page that moved
  // the cursor nowhere: its records sit ahead of the cursor (a seq between
  // is not in storage yet), so reading the same page again would too. The
  // cursor stays where it is, and the next push or pong retries from there.
  // A fetch refused as truncated is a resync instead.
  //
  // A page is routed one record per entity (below), but a record a person is
  // told about (`isAnnounced`, a reminder) is kept whatever follows it,
  // across every page, and handed to `announce` once the replay ends, however
  // it ends: the cursor has moved past it, so no later replay reads it again.
  const replay = async (after: number): Promise<void> => {
    const kept: Envelope[] = [];
    try {
      let from = after;
      while (!stopped) {
        let page: EventView[];
        try {
          page = await deps.fetchEventsAfter(from, deps.pageSize);
        } catch (error) {
          const head = truncatedHead(error);
          if (head !== null) await resync(head);
          return;
        }
        if (stopped) return;
        // One route per entity, not per record: routing invalidates every query
        // the entity is read from, so a page of two hundred task records that
        // each triggered a route would cancel and restart the list refetch two
        // hundred times over. The last record of an entity is the one routed,
        // and every record still moves the cursor.
        const last = new Map<string, Envelope>();
        for (const event of page) {
          apply(eventEnvelope(event), (routed) => {
            last.set(entityOf(event.kind), routed);
            if (isAnnounced(routed)) kept.push(routed);
          });
        }
        for (const envelope of last.values()) routeReplayed(envelope);
        if (isLastPage(page.length, deps.pageSize) || cursor === null || cursor <= from) return;
        from = cursor;
      }
    } finally {
      announceKept(kept);
    }
  };

  const deliver = async (envelope: Envelope) => {
    // A pong names the head; a head past the cursor is a gap no frame announced.
    if (envelope.type === "pong") {
      const after = behind(cursor, envelope.seq);
      if (after !== null) await replay(after);
      return;
    }
    const gap = apply(envelope);
    if (gap === null) return;
    await replay(gap);
    if (stopped) return;
    if (apply(envelope) === null) return;
    // Still ahead of the cursor: the push is worth routing either way, but
    // the cursor never moves past seqs that were not replayed, so the next
    // push or pong retries the fetch from where it stands.
    deps.route(envelope);
  };

  const enqueue = (work: () => Promise<void>) => {
    inbox = inbox.then(() => stopped ? undefined : work()).catch(() => undefined);
  };

  // What a reconnect and a degraded poll both do: catch up from the cursor.
  // With no cursor yet (no hello ever arrived) there is nothing to replay
  // from, and the cache is refreshed wholesale.
  const catchUp = async () => {
    if (cursor === null) {
      await deps.refreshAll();
      return;
    }
    await replay(cursor);
  };

  // The first hello's catch-up. What the page read before the socket
  // subscribed may predate a change the socket never pushed, so the stream's
  // tail is read and the records produced since the page began reading are
  // routed: the queries they touch refetch, and the rest stay as read. When
  // the tail cannot tell, or the hello carries no time, the cache is
  // refreshed wholesale instead.
  const firstCatchUp = async (head: number, sentAt: string | null | undefined, waitedMs: number) => {
    if (!sentAt) {
      await deps.refreshAll();
      return;
    }
    if (head <= 0) return;
    const after = Math.max(0, head - deps.pageSize);
    let tail: EventView[];
    try {
      tail = await deps.fetchEventsAfter(after, deps.pageSize);
    } catch {
      await deps.refreshAll();
      return;
    }
    if (stopped) return;
    const recent = tailSince(tail.filter((event) => event.seq <= head), after, readsBegan(sentAt, waitedMs));
    if (recent === null) {
      await deps.refreshAll();
      return;
    }
    const last = new Map<string, Envelope>();
    const kept: Envelope[] = [];
    for (const event of recent) {
      const envelope = eventEnvelope(event);
      last.set(entityOf(event.kind), envelope);
      if (isAnnounced(envelope)) kept.push(envelope);
    }
    for (const envelope of last.values()) routeReplayed(envelope);
    announceKept(kept);
  };

  const stopPolling = () => {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = null;
  };

  const startPolling = () => {
    if (pollTimer) return;
    pollTimer = setInterval(() => enqueue(catchUp), DEGRADED_POLL_INTERVAL_MS);
  };

  const clearStableTimer = () => {
    if (stableTimer) clearTimeout(stableTimer);
    stableTimer = null;
  };

  // A socket counts as connected once the server has spoken (the hello) or
  // has kept it open for a while; only then does the backoff start over. A
  // server that accepts and closes at once never gets there, so each try
  // waits longer than the last.
  const settle = () => {
    clearStableTimer();
    if (attempt === 0 && deps.connection.getState().status === "open") return;
    attempt = 0;
    connection.reset();
    stopPolling();
  };

  // A drop is a status change on its own: the store never says open without
  // a socket, whatever the backoff and the ticket request go on to do. The
  // first failed cycle is a reconnect in progress; from the second on the
  // channel is degraded and polls until a socket settles.
  const scheduleReconnect = () => {
    if (stopped) return;
    attempt += 1;
    connection.recordFailure();
    if (deps.connection.getState().failedCycles > 1) {
      connection.setStatus("degraded");
      startPolling();
    } else {
      connection.setStatus("connecting");
    }
    reconnectTimer = setTimeout(() => void connect(), backoffDelay(attempt));
  };

  // The first connect says so; a reconnect keeps the status its drop set.
  const connect = async () => {
    if (stopped || paused) return;
    const began = generation;
    if (attempt === 0) connection.setStatus("connecting");
    let ticket: string;
    try {
      ticket = await deps.requestTicket();
    } catch {
      if (stopped || began !== generation) return;
      scheduleReconnect();
      return;
    }
    if (stopped || began !== generation) return;
    const opened = deps.openSocket(ticket);
    socket = opened;
    opened.onopen = () => {
      if (stopped || socket !== opened) return;
      for (const topic of TOPICS) send({ op: "subscribe", topic });
      // A reconnect replays from its cursor at once; a first socket waits for
      // the hello, which names the stream's head.
      if (cursor !== null) enqueue(catchUp);
      pingTimer = setInterval(() => send({ op: "ping" }), PING_INTERVAL_MS);
      stableTimer = setTimeout(() => {
        // Open this long without a hello: there is no head to read the tail
        // up to, so the one catch-up left is the wholesale one.
        if (cursor === null) enqueue(catchUp);
        settle();
      }, STABLE_OPEN_MS);
    };
    opened.onmessage = (message) => {
      if (stopped || socket !== opened) return;
      const envelope = parseEnvelope(String(message.data));
      if (!envelope) return;
      if (envelope.type === "hello") {
        // The hello names the stream position, so a reconnect before the
        // first push replays from it instead of refreshing wholesale.
        if (cursor === null) {
          cursor = envelope.seq;
          const waited = now() - startedAt;
          enqueue(() => firstCatchUp(envelope.seq, envelope.sent_at, waited));
        }
        settle();
      }
      enqueue(() => deliver(envelope));
    };
    opened.onclose = (event) => {
      if (stopped || socket !== opened) return;
      if (pingTimer) clearInterval(pingTimer);
      pingTimer = null;
      clearStableTimer();
      if (socket === opened) socket = null;
      if (event.code === CLOSE_UNAUTHENTICATED) {
        // No reconnect follows, so the status says so before the sign-out.
        connection.close();
        deps.onUnauthenticated?.();
        return;
      }
      if (!stopped) scheduleReconnect();
    };
    opened.onerror = () => opened.close();
  };

  const clearPauseTimer = () => {
    if (pauseTimer) clearTimeout(pauseTimer);
    pauseTimer = null;
  };

  // Everything the socket runs on stops: the socket, its ping, a pending
  // reconnect, the degraded polling. The socket is let go before it is
  // closed, so its close is not read as a drop.
  const quiesce = () => {
    if (pingTimer) clearInterval(pingTimer);
    pingTimer = null;
    if (reconnectTimer) clearTimeout(reconnectTimer);
    reconnectTimer = null;
    clearStableTimer();
    stopPolling();
    const closing = socket;
    socket = null;
    closing?.close();
  };

  // A pause is not a failure: no reconnect is scheduled, no failed cycle is
  // counted, and the cursor stays where it is for the return to replay from.
  const pause = () => {
    pauseTimer = null;
    if (stopped || paused) return;
    paused = true;
    generation += 1;
    attempt = 0;
    quiesce();
    connection.pause();
  };

  void connect();

  return {
    stop: () => {
      if (stopped) return;
      stopped = true;
      clearPauseTimer();
      quiesce();
      connection.close();
    },
    hide: () => {
      if (stopped || paused || pauseTimer) return;
      pauseTimer = setTimeout(pause, HIDDEN_PAUSE_MS);
    },
    show: () => {
      clearPauseTimer();
      if (stopped || !paused) return;
      paused = false;
      // A first connect, with a fresh ticket; its open replays from the cursor.
      void connect();
    },
    cursor: () => cursor,
  };
}
