import { existsSync, readFileSync } from "node:fs";
import { expect, test } from "vitest";
import environments from "../../../deployment/cloud/environments.json";
import { fillLinks, linksFor } from "./links";

const page = (name: string) => readFileSync(new URL(`../${name}`, import.meta.url), "utf8");

test("each deployed build links to its own environment's app", () => {
  expect(linksFor("staging", environments)).toEqual({
    app: "https://app.staging.acme.example",
    site: "https://staging.acme.example",
    github: "https://github.com/acme-org/acme",
  });
  expect(linksFor("production", environments).app).toBe("https://app.acme.example");
  expect(linksFor("production", environments).site).toBe("https://acme.example");
});

test("a build for no known environment is refused", () => {
  expect(() => linksFor("prod", environments)).toThrow(/staging or production/);
});

test("a placeholder no link fills fails the build", () => {
  const links = linksFor("staging", environments);
  expect(fillLinks('<a href="%APP_URL%/login">', links)).toBe('<a href="https://app.staging.acme.example/login">');
  expect(() => fillLinks('<a href="%DOCS_URL%">', links)).toThrow(/%DOCS_URL%/);
});

// The page loads nothing from another origin: no script, no font, no image,
// no stylesheet. Only a link a person follows may leave the site.
test.each(["index.html", "404.html", "privacy.html"])("%s loads nothing from another origin and runs no script", (name) => {
  const html = page(name);
  expect(html).not.toMatch(/<script\b/i);
  expect(html).not.toMatch(/<iframe\b/i);
  for (const match of html.matchAll(/<(?:link|img|source|video|audio)\b[^>]*\b(?:href|src|srcset)="([^"]+)"/gi)) {
    const url = match[1] ?? "";
    const followed = /rel="canonical"/.test(match[0]);
    if (!followed) expect(url, `${name}: ${match[0]}`).not.toMatch(/^(https?:)?\/\//);
  }
});

test("the sign-in and sign-up links go to the app of the build's environment", () => {
  const html = page("index.html");
  expect(html).toContain('href="%APP_URL%/login"');
  expect(html).toContain('href="%APP_URL%/login?screen_hint=sign-up"');
  expect(html).toContain('href="%GITHUB_URL%"');
});

test("the page names the product and says what it is, with no picture", () => {
  const html = page("index.html");
  const text = html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");
  expect(text).toMatch(/Acme/);
  expect(text).toMatch(/A multi-tenant system for teams\./);
  expect(html).not.toMatch(/<(?:img|picture|video)\b/i);
});

// The tab shows the mark: the SVG, a 32-pixel PNG for a browser that takes no
// SVG icon, and the home-screen icon, each a file in public/.
test.each(["index.html", "404.html"])("%s links the icons, and each is in public/", (name) => {
  const html = page(name);
  expect(html).toContain('<link rel="icon" href="/favicon.svg" type="image/svg+xml" />');
  expect(html).toContain('<link rel="icon" href="/favicon-32.png" type="image/png" sizes="32x32" />');
  expect(html).toContain('<link rel="apple-touch-icon" href="/apple-touch-icon.png" />');
  for (const file of ["favicon.svg", "favicon-32.png", "apple-touch-icon.png"]) {
    expect(existsSync(new URL(`../public/${file}`, import.meta.url)), file).toBe(true);
  }
});

test("the privacy page is built, linked from the home page, and names who to ask", () => {
  expect(existsSync(new URL("../privacy.html", import.meta.url))).toBe(true);
  expect(page("index.html")).toContain('href="/privacy.html"');
  const text = page("privacy.html").replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");
  expect(text).toMatch(/Questions go to \S+@\S+/);
  for (const processor of ["Amazon Web Services", "WorkOS", "Sentry"]) {
    expect(text).toContain(processor);
  }
});
