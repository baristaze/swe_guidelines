// Pure: which time zone the portal records for the person. The identity
// holds it for whatever the server does at the person's local hour, so the
// portal sends the browser's own IANA name whenever it differs.

/** The browser's IANA time zone ("Europe/Istanbul"), or null when it names none. */
export function browserTimeZone(): string | null {
  try {
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    return zone ? zone : null;
  } catch {
    return null;
  }
}

/** The zone to send, or null when there is nothing to send: the browser
 * names none, or the identity holds that one already. */
export function zoneToSend(browser: string | null, stored: string | null | undefined): string | null {
  if (!browser) return null;
  return browser === stored ? null : browser;
}
