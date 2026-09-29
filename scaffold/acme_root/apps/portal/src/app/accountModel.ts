// Pure: what the account menu's button shows.

/** The part of an address before the @, for a window with no room for the
 * whole of it; an address with no @ is shown whole. */
export function shortEmail(email: string): string {
  const at = email.indexOf("@");
  return at > 0 ? email.slice(0, at) : email;
}
