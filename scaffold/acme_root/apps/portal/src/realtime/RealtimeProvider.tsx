// One provider owns the socket for the whole app; the loop itself lives in
// channel.ts, without React, and this component gives it the query cache, the
// transport client, the connection store, and the page's visibility, and
// shows the degraded banner. A paused socket shows none: it is not a failure.
import { useQueryClient } from "@tanstack/react-query";
import { ApiError, type IssuedTicketView } from "../api";
import { useEffect, type ReactNode } from "react";
import { api } from "../app/api";
import { forgetSessionIfHeld } from "../app/forgetSession";
import { Banner } from "../design/kit";
import { EVENTS_PAGE, fetchEventsAfter } from "../queries/events";
import { heldTask, placeTask, refreshTaskLists, removeTask, taskStamp } from "../queries/taskCache";
import { fetchTask } from "../queries/tasks";
import { useConnectionStore } from "../store/connection";
import { notify } from "../store/notices";
import { useSessionStore } from "../store/session";
import { openChannel } from "./channel";
import type { Envelope } from "./envelopes";
import { announceMissedReminders, announceReminder } from "./reminder";
import { watchPage } from "./pageVisibility";
import { reminderOf, routeEnvelope } from "./router";
import { createTaskHints, ReadAsList } from "./taskHints";

export function RealtimeProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const token = useSessionStore((s) => s.token);
  const status = useConnectionStore((s) => s.status);

  useEffect(() => {
    if (!token) return;
    const hints = createTaskHints({
      readTask: fetchTask,
      held: (id, version) => heldTask(queryClient, id, version),
      isGone: (cause) => cause instanceof ApiError && cause.status === 404,
      stamp: () => taskStamp(queryClient),
      place: (task, since) => placeTask(queryClient, task, { since }),
      remove: (id, since) => removeTask(queryClient, id, { since }),
      refreshLists: () => refreshTaskLists(queryClient),
    });
    // The reminder's title comes from the read the push already made; a
    // burst read as lists reads the task on its own.
    const readReminded = async (id: string) => {
      let task: Awaited<ReturnType<typeof hints.hint>>;
      try {
        task = await hints.hint(id);
      } catch (cause) {
        if (cause instanceof ReadAsList) return fetchTask(id);
        throw cause;
      }
      if (!task) throw new Error("the task is gone");
      return task;
    };
    const announce = (envelope: Envelope) => {
      const reminded = reminderOf(envelope);
      if (reminded) void announceReminder(reminded, { readTask: readReminded, notify });
    };
    const channel = openChannel({
      requestTicket: async () => (await api.post<IssuedTicketView>("/v1/realtime/tickets")).ticket,
      openSocket: (ticket) =>
        new WebSocket(api.websocketUrl(`/v1/realtime?ticket=${encodeURIComponent(ticket)}`)),
      fetchEventsAfter,
      route: (envelope) => {
        routeEnvelope(queryClient, envelope, hints);
        announce(envelope);
      },
      routeReplayed: (envelope) => routeEnvelope(queryClient, envelope),
      // A reminder read back from the stream (the replay after a reconnect,
      // the first catch-up) is kept through the replay's collapse to one
      // record per entity, and announced once the read-back ends.
      isAnnounced: (envelope) => reminderOf(envelope) !== null,
      announce: (envelopes) => {
        const reminded = envelopes.map(reminderOf).filter((id): id is string => id !== null);
        void announceMissedReminders(reminded, { readTask: fetchTask, notify });
      },
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
      hints.stop();
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
