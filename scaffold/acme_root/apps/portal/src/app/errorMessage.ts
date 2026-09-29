// Pure: the one line a screen shows for a failed call. A refusal (a 4xx) is
// something the person can act on, so it is said as a sentence and nothing
// more. A fault (a 5xx, or an answer the API never classified) is not theirs
// to fix, so it carries the request id as a reference to quote when reporting
// it. Anything else is the error's own words, or the caller's.
import { ApiError } from "../api";

/** The server's words as a sentence: a capital first letter and a full stop. */
export function asSentence(text: string): string {
  const trimmed = text.trim();
  if (!trimmed) return trimmed;
  const capital = trimmed.charAt(0).toUpperCase() + trimmed.slice(1);
  return /[.!?]$/.test(capital) ? capital : `${capital}.`;
}

export function errorMessage(caught: unknown, fallback: string): string {
  if (caught instanceof ApiError) {
    const said = asSentence(caught.message) || fallback;
    if (caught.status >= 400 && caught.status < 500) return said;
    return caught.requestId ? `${said} Reference: ${caught.requestId}` : said;
  }
  if (caught instanceof Error && caught.message) return caught.message;
  return fallback;
}
