// Transient notices: what a screen says when a write failed and there is no
// row to show it on, and what a change of many rows did, with the way to
// undo it. Purely client side; an entry leaves when dismissed, when its
// action runs, or after a while on its own.
import { create } from "zustand";

export const NOTICE_TTL_MS = 8_000;

/** How long a notice that offers to undo a change stays: long enough to
 * read it and change one's mind, short enough that the undo is still about
 * the change just made. */
export const UNDO_TTL_MS = 10_000;

/** A notice's one action: its label, and what it does. */
export interface NoticeAction {
  label: string;
  run: () => void;
}

/** A notice says something went wrong (`problem`, the default), or that a
 * change the person asked for is done (`done`). */
export type NoticeTone = "problem" | "done";

export interface Notice {
  id: string;
  message: string;
  tone: NoticeTone;
  action?: NoticeAction;
}

export interface NotifyOptions {
  tone?: NoticeTone;
  action?: NoticeAction;
  /** How long it stays; the default is `NOTICE_TTL_MS`. */
  ttlMs?: number;
}

interface NoticesState {
  notices: Notice[];
  /** Adds one; returns its id. */
  notify: (message: string, options?: NotifyOptions) => string;
  dismiss: (id: string) => void;
  /** Runs the notice's action, once, and lets the notice go. */
  act: (id: string) => void;
}

let sequence = 0;

export const useNoticesStore = create<NoticesState>()((set, get) => ({
  notices: [],
  notify: (message, options = {}) => {
    sequence += 1;
    const id = `notice-${sequence}`;
    const notice: Notice = { id, message, tone: options.tone ?? "problem", ...(options.action ? { action: options.action } : {}) };
    set((s) => ({ notices: [...s.notices, notice] }));
    setTimeout(() => get().dismiss(id), options.ttlMs ?? NOTICE_TTL_MS);
    return id;
  },
  dismiss: (id) => set((s) => ({ notices: s.notices.filter((n) => n.id !== id) })),
  act: (id) => {
    const notice = get().notices.find((n) => n.id === id);
    get().dismiss(id);
    notice?.action?.run();
  },
}));

/** For code outside React: the one store's `notify`. */
export function notify(message: string, options?: NotifyOptions): string {
  return useNoticesStore.getState().notify(message, options);
}
