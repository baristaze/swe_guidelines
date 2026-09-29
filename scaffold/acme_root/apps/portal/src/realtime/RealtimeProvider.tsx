// One provider owns the socket for the whole app; the loop itself lives in
// channel.ts, without React, and this component gives it the query cache, the
// transport client, the connection store, and the page's visibility, and
// shows the degraded banner. A paused socket shows none: it is not a failure.
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, type ReactNode } from "react";
import type { IssuedTicketView } from "@acme/client";
import { api } from "../app/api";
import { forgetSessionIfHeld } from "../app/forgetSession";
import { Banner } from "../design/kit";
import { EVENTS_PAGE, fetchEventsAfter } from "../queries/events";
import { useConnectionStore } from "../store/connection";
import { useSessionStore } from "../store/session";
import { openChannel } from "./channel";
import { watchPage } from "./pageVisibility";
import { routeEnvelope } from "./router";

export function RealtimeProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const token = useSessionStore((s) => s.token);
  const status = useConnectionStore((s) => s.status);

  useEffect(() => {
    if (!token) return;
    const channel = openChannel({
      requestTicket: async () => (await api.post<IssuedTicketView>("/v1/realtime/tickets")).ticket,
      openSocket: (ticket) =>
        new WebSocket(api.websocketUrl(`/v1/realtime?ticket=${encodeURIComponent(ticket)}`)),
      fetchEventsAfter,
      route: (envelope) => void routeEnvelope(queryClient, envelope),
      refreshAll: () => queryClient.invalidateQueries(),
      connection: useConnectionStore,
      pageSize: EVENTS_PAGE,
      // A socket opened under a session a switch has ended closes with 4401
      // too; only a close for the session the tab still holds signs it out.
      onUnauthenticated: () => forgetSessionIfHeld(token),
    });
    // A hidden tab's socket pauses, and any sign of the person's return
    // (shown, restored, focused, back online) resumes it. A session that
    // expired meanwhile is refused its ticket with a 401, which signs out.
    const unwatch = watchPage({ document, window }, channel);
    return () => {
      unwatch();
      channel.stop();
    };
  }, [token, queryClient]);

  return (
    <>
      {status === "degraded" ? (
        <Banner>Live updates are unavailable; refreshing every 30 seconds until they return.</Banner>
      ) : null}
      {children}
    </>
  );
}
