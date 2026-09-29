// A choice that sends a request is acted on once. While its request is in
// flight, another click on the same choices is dropped, not queued: a
// double-click, or clicks the browser held while the page was busy and then
// delivered together, would otherwise each send one.

export interface InFlight {
  busy: boolean;
}

export function inFlight(): InFlight {
  return { busy: false };
}

/** Runs `work` unless one is already running under `gate`; a dropped call answers undefined. */
export async function oneAtATime<T>(gate: InFlight, work: () => Promise<T>): Promise<T | undefined> {
  if (gate.busy) return undefined;
  gate.busy = true;
  try {
    return await work();
  } finally {
    gate.busy = false;
  }
}
