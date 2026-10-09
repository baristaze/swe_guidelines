// A push is a hint: it names one record, and the version its change wrote
// when the record carries one, and nothing else of it. The reader reads that
// one record through the authorized read and places the answer in the query
// cache; a read that finds nothing removes it. A record the cache already
// holds at that version is not read: the tab that wrote it placed the write's
// answer, and the push is about that answer. Hints that arrive together are
// read together, so a record named twice is read once, and a burst past a
// bound reads the collections once instead of record by record. The reader
// knows no entity: everything it reaches for is handed in, so one reader
// serves any entity with a read of one record, and it runs in a test with
// fake timers and no query cache.

/** How long a window stays open after its last new record. The pushes of
 * one write that touches many records arrive milliseconds apart, and a
 * person's own writes seconds apart. */
export const HINT_WINDOW_MS = 100;

/** How long a window stays open at most, so a steady stream of pushes is
 * still read while it lasts. */
export const HINT_WINDOW_MAX_MS = 500;

/** Past this many records in one window, the collections are read once
 * instead of each record on its own: twenty reads of one record already cost
 * more than reading the collections the entity is shown in. */
export const HINT_BURST = 20;

export interface HintEffects<T> {
  /** The authorized read of one record. */
  read(id: string): Promise<T>;
  /** Whether a failed read says the record is not there to see (a 404). */
  isGone(cause: unknown): boolean;
  /** Whether the cache holds the record at `version` or past it. */
  holds(id: string, version: number): boolean;
  /** The clock when a window's reads are issued; handed back with each
   * answer, so a late answer can tell it is late. */
  stamp(): number;
  /** Places what one read answered. */
  place(record: T, since: number): void;
  /** Takes out a record a read found nothing of. */
  remove(id: string, since: number): void;
  /** Reads every collection the entity is shown in again, once: for a burst,
   * or a read that failed for a reason that leaves the record's place unknown. */
  readCollections(): void;
}

export interface Hints {
  /** Notes a push about one record, with the version its change wrote when
   * the push names one. */
  hint(id: string, version?: number): void;
  /** Ends the reader: a window still open is dropped, and an answer still on
   * its way is not placed. */
  stop(): void;
}

export function createHints<T>(effects: HintEffects<T>): Hints {
  // Each record of the open window, with the newest version its pushes
  // named; null once a push named none, since then only a read can tell.
  let gathered = new Map<string, number | null>();
  let quiet: ReturnType<typeof setTimeout> | null = null;
  let longest: ReturnType<typeof setTimeout> | null = null;
  let stopped = false;

  const close = () => {
    if (quiet) clearTimeout(quiet);
    if (longest) clearTimeout(longest);
    quiet = longest = null;
  };

  const flush = async () => {
    close();
    const batch = gathered;
    gathered = new Map();
    if (stopped) return;
    // Checked when the window closes, not when the push arrives: the push of
    // this tab's own write can come before the write's answer does.
    for (const [id, version] of batch) {
      if (version !== null && effects.holds(id, version)) batch.delete(id);
    }
    if (batch.size === 0) return;
    if (batch.size > HINT_BURST) {
      effects.readCollections();
      return;
    }
    const since = effects.stamp();
    const ids = [...batch.keys()];
    const answers = await Promise.allSettled(ids.map(async (id) => effects.read(id)));
    if (stopped) return;
    // One window's answers are placed together, once every read is back.
    let unknown = false;
    answers.forEach((answer, index) => {
      const id = ids[index]!;
      if (answer.status === "fulfilled") effects.place(answer.value, since);
      else if (effects.isGone(answer.reason)) effects.remove(id, since);
      else unknown = true;
    });
    if (unknown) effects.readCollections();
  };

  return {
    hint(id, version) {
      if (stopped) return;
      const named = version ?? null;
      if (gathered.has(id)) {
        const held = gathered.get(id)!;
        gathered.set(id, held === null || named === null ? null : Math.max(held, named));
        return;
      }
      gathered.set(id, named);
      if (quiet) clearTimeout(quiet);
      quiet = setTimeout(() => void flush(), HINT_WINDOW_MS);
      longest ??= setTimeout(() => void flush(), HINT_WINDOW_MAX_MS);
    },
    stop() {
      stopped = true;
      close();
      gathered = new Map();
    },
  };
}
