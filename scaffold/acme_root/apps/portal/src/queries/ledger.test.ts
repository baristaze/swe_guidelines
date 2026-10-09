// The ledger alone: which late answers it refuses.
import { describe, expect, it } from "vitest";
import { createLedger } from "./ledger";

describe("a late answer", () => {
  it("never overwrites an answer from a read issued after it, for a record with no version", () => {
    const ledger = createLedger();
    const first = ledger.stamp();
    const second = ledger.stamp();
    expect(ledger.admit("a", null, second)).toBe(true);
    expect(ledger.admit("a", null, first)).toBe(false);
    expect(ledger.admit("a", null, ledger.stamp())).toBe(true);
  });

  it("never overwrites a newer version, whichever read was issued first", () => {
    const ledger = createLedger();
    const first = ledger.stamp();
    const second = ledger.stamp();
    expect(ledger.admit("a", 5, first)).toBe(true);
    expect(ledger.admit("a", 4, second)).toBe(false);
    expect(ledger.admit("a", 5, second)).toBe(true);
    expect(ledger.admit("a", 6, first)).toBe(true);
  });

  it("from a read issued before a removal never brings the record back", () => {
    const ledger = createLedger();
    const before = ledger.stamp();
    expect(ledger.admitGone("a", ledger.stamp())).toBe(true);
    expect(ledger.admit("a", 9, before)).toBe(false);
    expect(ledger.admit("a", 9, ledger.stamp())).toBe(true);
  });

  it("that found nothing never removes what a read issued after it placed", () => {
    const ledger = createLedger();
    const before = ledger.stamp();
    expect(ledger.admit("a", null, ledger.stamp())).toBe(true);
    expect(ledger.admitGone("a", before)).toBe(false);
  });

  it("of one record leaves every other record alone", () => {
    const ledger = createLedger();
    const first = ledger.stamp();
    expect(ledger.admit("a", null, ledger.stamp())).toBe(true);
    expect(ledger.admit("b", null, first)).toBe(true);
  });
});

describe("holds", () => {
  it("says a record is held at the version placed and every version before it, until it is removed", () => {
    const ledger = createLedger();
    expect(ledger.holds("a", 1)).toBe(false);
    ledger.admit("a", 3, ledger.stamp());
    expect(ledger.holds("a", 3)).toBe(true);
    expect(ledger.holds("a", 2)).toBe(true);
    expect(ledger.holds("a", 4)).toBe(false);
    ledger.admitGone("a", ledger.stamp());
    expect(ledger.holds("a", 3)).toBe(false);
  });

  it("never says a record with no version is held", () => {
    const ledger = createLedger();
    ledger.admit("a", null, ledger.stamp());
    expect(ledger.holds("a", 0)).toBe(false);
  });
});
