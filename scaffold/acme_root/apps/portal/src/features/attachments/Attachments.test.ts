// @vitest-environment jsdom
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import type { AttachmentsVm } from "./useAttachmentsVm";

const vm: AttachmentsVm = {
  loading: false,
  rows: [
    { id: "f1", name: "plan.zip", size: "1.5 KB", kind: "ZIP", contentType: "application/zip", preview: null },
    { id: "f2", name: "photo.png", size: "2.0 MB", kind: "PNG", contentType: "image/png", preview: "image" },
  ],
  hasMore: false,
  uploading: [{ key: "k1", name: "big.zip", size: "20.0 MB" }],
  busyId: null,
  upload: vi.fn(async () => undefined),
  download: vi.fn(async () => undefined),
  destroy: vi.fn(async () => undefined),
};
vi.mock("./useAttachmentsVm", () => ({ useAttachmentsVm: () => vm }));
vi.mock("./FilePreview", () => ({
  FilePreview: ({ fileId, kind }: { fileId: string; kind: string }) => createElement("figure", { "data-file": fileId }, kind),
}));

const { Attachments } = await import("./Attachments");

vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
const container = document.createElement("div");
document.body.append(container);
const root = createRoot(container);
afterEach(async () => {
  await act(async () => root.render(null));
});

const buttons = (label: string) =>
  [...container.querySelectorAll("button")].filter((b) => b.textContent === label);

it("lists each file with its name, size, and type, and the uploads in flight", async () => {
  await act(async () => root.render(createElement(Attachments, { taskId: "t1", canWrite: true })));
  const text = container.textContent ?? "";
  for (const part of ["plan.zip", "1.5 KB", "ZIP", "photo.png", "2.0 MB", "PNG", "big.zip", "uploading 20.0 MB"]) {
    expect(text).toContain(part);
  }
  buttons("download")[1]!.click();
  expect(vm.download).toHaveBeenCalledWith("f2");
  buttons("remove")[0]!.click();
  expect(vm.destroy).toHaveBeenCalledWith("f1");
});

it("uploads what is chosen in the picker", async () => {
  await act(async () => root.render(createElement(Attachments, { taskId: "t1", canWrite: true })));
  const input = container.querySelector<HTMLInputElement>('input[type="file"]')!;
  const chosen = new File(["%PDF!"], "spec.pdf", { type: "application/pdf" });
  Object.defineProperty(input, "files", { value: [chosen], configurable: true });
  await act(async () => input.dispatchEvent(new Event("change", { bubbles: true })));
  expect(vm.upload).toHaveBeenCalledWith([chosen]);
});

it("offers no picker and no remove to someone who cannot write", async () => {
  await act(async () => root.render(createElement(Attachments, { taskId: "t1", canWrite: false })));
  expect(container.querySelector('input[type="file"]')).toBeNull();
  expect(buttons("remove")).toEqual([]);
  expect(buttons("download")).toHaveLength(2);
});

it("shows a preview for a file that has one, and none for one that does not", async () => {
  await act(async () => root.render(createElement(Attachments, { taskId: "t1", canWrite: false })));
  const figures = [...container.querySelectorAll("figure")];
  expect(figures.map((f) => [f.dataset.file, f.textContent])).toEqual([["f2", "image"]]);
});
