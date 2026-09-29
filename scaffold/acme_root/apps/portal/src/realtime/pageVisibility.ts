// Tells the channel when the tab is hidden and when it is back. The channel
// decides what a hide costs (a pause after HIDDEN_PAUSE_MS) and what a return
// does; this file only listens. The page is handed in, so a test drives it
// with a fake document and window.
import type { Channel } from "./channel";

type Listen = (type: string, listener: (event: Event) => void) => void;

/** What is read of the page; a browser's document and window satisfy it. */
export interface PageLike {
  document: { readonly hidden: boolean; addEventListener: Listen; removeEventListener: Listen };
  window: { addEventListener: Listen; removeEventListener: Listen };
}

/** The window's events that each mean the person may be looking again. A
 * `pageshow` covers a page restored from the back/forward cache too. */
const RETURNS = ["pageshow", "focus", "online"] as const;

/**
 * Hides and shows the channel with the page, and returns the unsubscribe.
 * A return signal shows the channel whatever the page says, since `show` is
 * idempotent; a return that finds the page still hidden (`online` in a
 * background tab) starts the hidden wait over, so the socket it brought back
 * does not stay open for good.
 */
export function watchPage(page: PageLike, channel: Pick<Channel, "hide" | "show">): () => void {
  const settle = () => {
    if (page.document.hidden) channel.hide();
  };
  const onVisibility = () => {
    if (page.document.hidden) channel.hide();
    else channel.show();
  };
  const onReturn = () => {
    channel.show();
    settle();
  };
  page.document.addEventListener("visibilitychange", onVisibility);
  for (const type of RETURNS) page.window.addEventListener(type, onReturn);
  // A tab opened in the background is hidden from its first render.
  settle();
  return () => {
    page.document.removeEventListener("visibilitychange", onVisibility);
    for (const type of RETURNS) page.window.removeEventListener(type, onReturn);
  };
}
