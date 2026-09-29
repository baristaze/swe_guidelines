// The object store, reached through a URL the API signed and handed over: a
// form an upload posts to, a link a download follows. It is not the API, so
// nothing of the API client goes with it: no bearer, no app headers, no
// retry. The form or the link is the credential, and it is good for one
// file for a few minutes.

/** A transfer moves a file, not a JSON body, so its deadline is its own: long
 * enough for the largest file on a slow link, and well inside the fifteen
 * minutes a form lives. */
export const TRANSFER_TIMEOUT_MS = 10 * 60 * 1000;

export function storeFetch(url: string, init: RequestInit = {}, fetchImpl: typeof fetch = fetch): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TRANSFER_TIMEOUT_MS);
  return fetchImpl(url, { ...init, signal: controller.signal }).finally(() => clearTimeout(timer));
}
