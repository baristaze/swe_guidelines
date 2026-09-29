// The tab shows the product's mark: index.html links the SVG, a 32-pixel PNG
// for a browser that takes no SVG icon, and the home-screen icon, and each
// is a file in public/, which the build copies to the root of dist/.
import { existsSync, readFileSync } from "node:fs";
import { expect, it } from "vitest";

const portal = new URL("../../", import.meta.url);
const html = readFileSync(new URL("index.html", portal), "utf8");
const links = [...html.matchAll(/<link\b[^>]*>/g)].map((match) => match[0]);
const hrefOf = (rel: string, type?: string) =>
  links
    .filter((link) => link.includes(`rel="${rel}"`) && (!type || link.includes(`type="${type}"`)))
    .map((link) => /href="([^"]+)"/.exec(link)?.[1]);

it("links the mark, its PNG, and the home-screen icon", () => {
  expect(hrefOf("icon", "image/svg+xml")).toEqual(["/favicon.svg"]);
  expect(hrefOf("icon", "image/png")).toEqual(["/favicon-32.png"]);
  expect(links.find((link) => link.includes("/favicon-32.png"))).toContain('sizes="32x32"');
  expect(hrefOf("apple-touch-icon")).toEqual(["/apple-touch-icon.png"]);
});

it("has every icon it links in public/, the mark the site's own", () => {
  for (const href of [...hrefOf("icon"), ...hrefOf("apple-touch-icon")]) {
    expect(existsSync(new URL(`public${href}`, portal)), href).toBe(true);
  }
  const site = readFileSync(new URL("../site/public/favicon.svg", portal), "utf8");
  expect(readFileSync(new URL("public/favicon.svg", portal), "utf8")).toBe(site);
});

it("draws the PNGs at the sizes they say", () => {
  const size = (name: string) => {
    const png = readFileSync(new URL(`public/${name}`, portal));
    return [png.readUInt32BE(16), png.readUInt32BE(20)];
  };
  expect(size("favicon-32.png")).toEqual([32, 32]);
  expect(size("apple-touch-icon.png")).toEqual([180, 180]);
});
