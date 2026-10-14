// The event stream is replayed, not subscribed to: the realtime provider
// asks for everything after the last seq it saw, one page at a time.
import type { change_events } from "@acme/client";
import { api } from "../app/api";

export const CHANGE_EVENTS_PAGE = 200;

export function fetchChangeEventsAfter(afterSeq: number, limit = CHANGE_EVENTS_PAGE): Promise<change_events.ChangeEventView[]> {
  return api.get<change_events.ChangeEventView[]>(`/v1/change-events?after_seq=${afterSeq}&limit=${limit}`);
}
