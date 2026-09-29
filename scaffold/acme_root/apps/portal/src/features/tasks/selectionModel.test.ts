import { describe, expect, it } from "vitest";
import { isSelected, NOTHING, pick, prune, range, selectAll, selectedCount, toggle } from "./selectionModel";

const open = ["a", "b", "c", "d", "e"];
const done = ["x", "y"];

describe("the selection", () => {
  it("turns one row on and off with ⌘-click, keeping the section's order", () => {
    let s = toggle(NOTHING, "open", "c", open);
    s = toggle(s, "open", "a", open);
    expect(s).toEqual({ section: "open", ids: ["a", "c"], anchor: "a", all: false });
    s = toggle(s, "open", "c", open);
    expect(s.ids).toEqual(["a"]);
    expect(toggle(s, "open", "a", open)).toEqual(NOTHING);
  });

  it("reaches from the anchor to the row a Shift-click names, either way, adding to the picks", () => {
    let s = toggle(NOTHING, "open", "b", open);
    s = range(s, "open", "d", open);
    expect(s.ids).toEqual(["b", "c", "d"]);
    expect(s.anchor).toBe("b");
    s = toggle(NOTHING, "open", "e", open);
    s = pick(s, "open", "c", open, "range");
    expect(s.ids).toEqual(["c", "d", "e"]);
    s = toggle(toggle(NOTHING, "open", "a", open), "open", "e", open);
    expect(range(s, "open", "c", open).ids).toEqual(["a", "c", "d", "e"]);
  });

  it("takes a Shift-click with no anchor as a pick of the one row", () => {
    expect(range(NOTHING, "open", "c", open)).toEqual({ section: "open", ids: ["c"], anchor: "c", all: false });
  });

  it("stays in one section: a pick in the other starts over there", () => {
    const s = range(toggle(NOTHING, "open", "a", open), "open", "c", open);
    expect(toggle(s, "done", "y", done)).toEqual({ section: "done", ids: ["y"], anchor: "y", all: false });
    expect(range(s, "done", "y", done)).toEqual({ section: "done", ids: ["y"], anchor: "y", all: false });
    expect(isSelected(s, "done", "a")).toBe(false);
  });

  it("selects every task of a section with Select all, and a pick after it narrows to the rows shown", () => {
    const s = selectAll("done");
    expect(isSelected(s, "done", "anything")).toBe(true);
    expect(isSelected(s, "open", "a")).toBe(false);
    expect(selectedCount(s, null)).toBeNull();
    expect(selectedCount(s, 40)).toBe(40);
    expect(range(toggle(s, "done", "x", done), "done", "y", done).ids).toEqual(["x", "y"]);
    expect(toggle(s, "done", "x", done)).toEqual({ section: "done", ids: ["y"], anchor: "x", all: false });
  });

  it("lets go of a row that left the section, and of the selection when none is left", () => {
    const s = range(toggle(NOTHING, "open", "a", open), "open", "c", open);
    expect(prune(s, open)).toBe(s);
    expect(prune(s, ["b", "d"])).toEqual({ section: "open", ids: ["b"], anchor: null, all: false });
    expect(prune(s, ["d"])).toEqual(NOTHING);
    expect(prune(selectAll("open"), [])).toEqual(selectAll("open"));
    expect(selectedCount(s, 99)).toBe(3);
  });
});
