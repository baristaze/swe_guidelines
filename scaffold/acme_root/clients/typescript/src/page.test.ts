import { describe, expect, it, vi } from "vitest";
import { pageJson } from "./page";

describe("pageJson", () => {
  it("reads the file past every cache, and answers its JSON", async () => {
    const fetchImpl = vi.fn<typeof fetch>(() =>
      Promise.resolve(
        new Response(JSON.stringify({ apiUrl: "" }), { headers: { "content-type": "application/json" } }),
      ),
    );
    expect(await pageJson("/config.json", fetchImpl)).toEqual({ apiUrl: "" });
    expect(fetchImpl).toHaveBeenCalledWith("/config.json", { cache: "no-store" });
  });

  it("answers null for the index.html a host serves in the file's place", async () => {
    const fetchImpl = () =>
      Promise.resolve(new Response("<!doctype html>", { headers: { "content-type": "text/html" } }));
    expect(await pageJson("/config.json", fetchImpl)).toBeNull();
  });

  it("answers null for a missing file, a body that does not parse, and a failed request", async () => {
    const missing = () => Promise.resolve(new Response("{}", { status: 404, headers: { "content-type": "application/json" } }));
    const broken = () => Promise.resolve(new Response("{", { headers: { "content-type": "application/json" } }));
    const failed = () => Promise.reject(new TypeError("Failed to fetch"));
    expect(await pageJson("/config.json", missing)).toBeNull();
    expect(await pageJson("/config.json", broken)).toBeNull();
    expect(await pageJson("/config.json", failed)).toBeNull();
  });
});
