// The event stream is replayed, not subscribed to: the realtime provider
// asks for everything after the last seq it saw, one page at a time.
import type { EventView } from "../api";
import { api } from "../app/api";

export const EVENTS_PAGE = 200;

export function fetchEventsAfter(afterSeq: number, limit = EVENTS_PAGE): Promise<EventView[]> {
  return api.get<EventView[]>(`/v1/events?after_seq=${afterSeq}&limit=${limit}`);
}
