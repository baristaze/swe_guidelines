// A file the page's own origin serves beside the bundle, such as the
// runtime config an app reads before its first render. It is not the API,
// so nothing of the API client goes with it: no bearer, no app headers, no
// retry.

/**
 * The file's JSON, or null when there is none: a failed request, another
 * status, or a body that is not JSON, such as the index.html a dev server or
 * a single-page host answers for a path it does not hold.
 */
export async function pageJson(path: string, fetchImpl: typeof fetch = fetch): Promise<unknown> {
  try {
    const response = await fetchImpl(path, { cache: "no-store" });
    if (response.ok && response.headers.get("content-type")?.includes("json")) return await response.json();
  } catch {
    // Nothing there, or nothing that reads: the caller's defaults apply.
  }
  return null;
}
