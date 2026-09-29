import { describe, expect, it } from "vitest";
import { dueBadge, dueOnChange, dueState, formatDue, parseDate, toInputValue } from "./dueModel";

// "Now" is a local moment, so the test reads the same in whatever zone it runs in.
const now = new Date(2026, 8, 22, 12, 0); // Tuesday 22 September 2026, 12:00
const late = new Date(2026, 8, 22, 23, 30); // the same day, well past nine

describe("due model", () => {
  it("reads a date as the calendar day it names, never shifted by a zone", () => {
    const date = parseDate("2026-09-30")!;
    expect([date.getFullYear(), date.getMonth(), date.getDate(), date.getHours()]).toEqual([2026, 8, 30, 0]);
    expect(parseDate("2026-01-01")!.getDate()).toBe(1);
    expect(parseDate(null)).toBeNull();
    expect(parseDate("2026-02-30")).toBeNull();
    expect(parseDate("2026-09-30T09:00:00Z")).toBeNull();
    expect(parseDate("not a date")).toBeNull();
  });

  it("says Today, Tomorrow, the weekday of the week ahead, and the date further out", () => {
    expect(formatDue(parseDate("2026-09-22")!, now)).toBe("Today");
    expect(formatDue(parseDate("2026-09-23")!, now)).toBe("Tomorrow");
    expect(formatDue(parseDate("2026-09-25")!, now)).toBe("Fri");
    expect(formatDue(parseDate("2026-09-28")!, now)).toBe("Mon");
    expect(formatDue(parseDate("2026-09-29")!, now)).toBe("Sep 29");
    expect(formatDue(parseDate("2026-09-30")!, now)).toBe("Sep 30");
    expect(formatDue(parseDate("2026-09-20")!, now)).toBe("Sep 20");
    expect(formatDue(parseDate("2027-01-04")!, now)).toBe("Jan 4 2027");
  });

  it("knows an upcoming, an overdue, and a reminded due date", () => {
    expect(dueState({ due_on: null, reminded_at: null }, now)).toBeNull();
    expect(dueState({ due_on: "2026-09-22", reminded_at: null }, late)).toBe("upcoming");
    expect(dueState({ due_on: "2026-09-30", reminded_at: null }, now)).toBe("upcoming");
    expect(dueState({ due_on: "2026-09-21", reminded_at: null }, now)).toBe("overdue");
    expect(dueState({ due_on: "2026-09-22", reminded_at: "2026-09-22T07:00:00Z" }, now)).toBe("reminded");
    expect(dueState({ due_on: "garbage", reminded_at: null }, now)).toBeNull();
  });

  it("builds the badge a row shows, with no time in it", () => {
    expect(dueBadge({ due_on: null, reminded_at: null }, now)).toBeNull();
    expect(dueBadge({ due_on: "2026-09-22", reminded_at: null }, now)).toEqual({
      state: "upcoming",
      text: "due Today",
      title: "Due Tuesday 22 September 2026",
    });
    expect(dueBadge({ due_on: "2026-09-23", reminded_at: null }, now)).toMatchObject({ text: "due Tomorrow" });
    expect(dueBadge({ due_on: "2026-09-30", reminded_at: null }, now)).toMatchObject({ text: "due Sep 30" });
    expect(dueBadge({ due_on: "2026-09-21", reminded_at: null }, now)).toEqual({
      state: "overdue",
      text: "Overdue · Sep 21",
      title: "Due Monday 21 September 2026. The date has passed.",
    });
    expect(dueBadge({ due_on: "2026-09-22", reminded_at: "2026-09-22T07:00:00Z" }, now)).toEqual({
      state: "reminded",
      text: "reminded · due Today",
      title: "Due Tuesday 22 September 2026. The reminder went out.",
    });
  });

  it("hands the date input the date itself", () => {
    expect(toInputValue(null)).toBe("");
    expect(toInputValue("2026-09-30")).toBe("2026-09-30");
    expect(toInputValue("bad")).toBe("");
  });

  it("sends a due date only when the edit changed it, and null to clear it", () => {
    expect(dueOnChange("", "")).toBeUndefined();
    expect(dueOnChange("2026-09-30", "2026-09-30")).toBeUndefined();
    expect(dueOnChange("2026-09-30", "")).toBeNull();
    expect(dueOnChange("", "2026-10-01")).toBe("2026-10-01");
  });
});
