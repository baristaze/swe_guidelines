// The sign-ins this tab started and has not finished. Each is kept under its
// `state`, the random value the tab sends to the identity provider and that
// comes back on the callback: a callback whose state this tab never stored is
// someone else's round trip, and is refused. The tab's session storage holds
// it, so it survives the provider's redirect and dies with the tab. A state
// is consumed once.

export const SIGN_IN_STATE_KEY = "acme.portal.signIn";

/** A sign-in started and not finished more than this long ago is dropped. */
export const SIGN_IN_STATE_TTL_MS = 30 * 60 * 1000;

export interface PendingSignIn {
  /** Where to land once signed in: an in-app absolute path. */
  returnTo: string;
  /** The token an invitation's link carried, handed back on the callback. */
  invitationToken: string | null;
  /** The PKCE verifier the API answered the start with: the provider holds
   * only its digest, so the code the callback brings is worth nothing
   * without it. It stays in this tab. */
  codeVerifier: string;
  startedAt: number;
}

type Store = Pick<Storage, "getItem" | "setItem">;

function sessionStore(): Store | undefined {
  return typeof sessionStorage === "undefined" ? undefined : sessionStorage;
}

function readAll(storage: Store | undefined): Record<string, PendingSignIn> {
  try {
    const raw = storage?.getItem(SIGN_IN_STATE_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : {};
    return typeof parsed === "object" && parsed !== null && !Array.isArray(parsed)
      ? (parsed as Record<string, PendingSignIn>)
      : {};
  } catch {
    return {};
  }
}

function writeAll(storage: Store | undefined, all: Record<string, PendingSignIn>): void {
  try {
    storage?.setItem(SIGN_IN_STATE_KEY, JSON.stringify(all));
  } catch {
    // Storage unavailable: the callback will not find the state and says so.
  }
}

function live(all: Record<string, PendingSignIn>, now: number): Record<string, PendingSignIn> {
  return Object.fromEntries(
    Object.entries(all).filter(([, pending]) => now - pending.startedAt < SIGN_IN_STATE_TTL_MS),
  );
}

/** A fresh state: 32 random bytes, base64url, no padding. */
export function newState(
  random: (bytes: Uint8Array<ArrayBuffer>) => Uint8Array<ArrayBuffer> = (b) => crypto.getRandomValues(b),
): string {
  const bytes = random(new Uint8Array(32));
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function rememberSignIn(
  state: string,
  pending: Omit<PendingSignIn, "startedAt">,
  now: number = Date.now(),
  storage: Store | undefined = sessionStore(),
): void {
  const all = live(readAll(storage), now);
  all[state] = { ...pending, startedAt: now };
  writeAll(storage, all);
}

/** The pending sign-in stored under `state`, removed in the same step, so a
 * callback replayed with the same state finds nothing. */
export function consumeSignIn(
  state: string,
  now: number = Date.now(),
  storage: Store | undefined = sessionStore(),
): PendingSignIn | null {
  const all = live(readAll(storage), now);
  const found = Object.prototype.hasOwnProperty.call(all, state) ? all[state] : undefined;
  delete all[state];
  writeAll(storage, all);
  return found ?? null;
}

// A person who just signed out lands on `/login`, which would otherwise start
// a sign-in at once and let the provider's own session carry them straight
// back in. The flag, held in memory for this page only, makes `/login` wait
// for them to ask.
let signedOut = false;

export function noteSignedOut(): void {
  signedOut = true;
}

/** Whether the tab just signed out; asking clears it. */
export function takeSignedOut(): boolean {
  const was = signedOut;
  signedOut = false;
  return was;
}

// A person who deleted their account lands on `/signed-out`, through the
// identity provider's logout or straight there, and the page says what
// became of the account. The flag rides the tab's session storage, since
// the provider's logout is a full page load away, and it is read once.

export const ACCOUNT_DELETED_KEY = "acme.portal.accountDeleted";

type Flag = Pick<Storage, "getItem" | "setItem" | "removeItem">;

function flagStore(): Flag | undefined {
  return typeof sessionStorage === "undefined" ? undefined : sessionStorage;
}

export function noteAccountDeleted(storage: Flag | undefined = flagStore()): void {
  try {
    storage?.setItem(ACCOUNT_DELETED_KEY, "1");
  } catch {
    // Storage unavailable: the page says "signed out" and nothing more.
  }
}

/** Whether this tab just deleted its account; asking clears it. */
export function takeAccountDeleted(storage: Flag | undefined = flagStore()): boolean {
  try {
    const was = storage?.getItem(ACCOUNT_DELETED_KEY) === "1";
    storage?.removeItem(ACCOUNT_DELETED_KEY);
    return was;
  } catch {
    return false;
  }
}
