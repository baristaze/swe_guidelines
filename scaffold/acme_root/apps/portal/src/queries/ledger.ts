// What the cache last heard of each record of one entity, so a late answer
// never overwrites a newer one. Answers land out of order: two reads of one
// record in flight at once, or a read that finds nothing beside one that
// finds it. Each read takes a stamp when it is issued, and the stamps only
// grow. An answer is placed unless the ledger holds a newer version of the
// record, or, where either side carries none, an answer or a removal from a
// read issued later. A read issued before a removal never brings the record
// back.

export interface Ledger {
  /** The next stamp: a read takes one when it is issued. */
  stamp(): number;
  /** Whether an answer at `version` (null for a record that carries none),
   * from a read issued at `since`, is the newest heard; when it is, the
   * ledger keeps it. */
  admit(id: string, version: number | null, since: number): boolean;
  /** Whether a read issued at `since` that found nothing is the newest
   * heard; when it is, the ledger keeps the removal. */
  admitGone(id: string, since: number): boolean;
  /** Whether the record was placed at `version` or past it, and not removed
   * since. */
  holds(id: string, version: number): boolean;
}

interface Heard {
  version: number | null;
  since: number;
  gone: boolean;
}

export function createLedger(): Ledger {
  let clock = 0;
  const heard = new Map<string, Heard>();
  return {
    stamp: () => (clock += 1),
    admit(id, version, since) {
      const last = heard.get(id);
      if (last) {
        const older =
          !last.gone && version !== null && last.version !== null ? version < last.version : since < last.since;
        if (older) return false;
      }
      heard.set(id, { version, since, gone: false });
      return true;
    },
    admitGone(id, since) {
      const last = heard.get(id);
      if (last && since < last.since) return false;
      heard.set(id, { version: last?.version ?? null, since, gone: true });
      return true;
    },
    holds(id, version) {
      const last = heard.get(id);
      return last !== undefined && !last.gone && last.version !== null && last.version >= version;
    },
  };
}
