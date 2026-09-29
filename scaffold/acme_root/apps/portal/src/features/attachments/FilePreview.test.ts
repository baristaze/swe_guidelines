// @vitest-environment jsdom
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";

const URL_OF = "https://store.example.test/o?X-Amz-Signature=s&response-content-disposition=inline";
vi.mock("../../queries/attachments", () => ({ usePreviewUrl: () => ({ data: URL_OF, isError: false }) }));
const { FilePreview } = await import("./FilePreview");

vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
const container = document.createElement("div");
document.body.append(container);
const root = createRoot(container);
afterEach(async () => {
  await act(async () => root.render(null));
});

const show = (kind: "image" | "video" | "audio" | "pdf") =>
  act(async () => root.render(createElement(FilePreview, { fileId: "f1", kind, name: "clip" })));

it("shows an image as a thumbnail that opens larger in the page, and closes", async () => {
  await show("image");
  expect(container.querySelector("img")!.getAttribute("src")).toBe(URL_OF);
  await act(async () => container.querySelector("button")!.click());
  expect(container.querySelector('[role="dialog"] img')!.getAttribute("src")).toBe(URL_OF);
  await act(async () => window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" })));
  expect(container.querySelector('[role="dialog"]')).toBeNull();
});

it("plays a video and a sound in the browser's own player", async () => {
  await show("video");
  const video = container.querySelector("video")!;
  expect([video.getAttribute("src"), video.hasAttribute("controls")]).toEqual([URL_OF, true]);
  await show("audio");
  const audio = container.querySelector("audio")!;
  expect([audio.getAttribute("src"), audio.hasAttribute("controls")]).toEqual([URL_OF, true]);
});

it("shows a PDF in a frame", async () => {
  await show("pdf");
  expect(container.querySelector("iframe")!.getAttribute("src")).toBe(URL_OF);
});
