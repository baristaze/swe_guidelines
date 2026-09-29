/// <reference types="vitest/config" />
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import environments from "../../deployment/cloud/environments.json";
import { fillLinks, linksFor } from "./src/links";

const repoRoot = fileURLToPath(new URL("../..", import.meta.url));

// A static page: HTML and CSS, no script. The build runs once per deployed
// environment (`--mode staging`, `--mode production`), each with the links
// of its environment written into the HTML.
export default defineConfig(({ mode }) => {
  const links = linksFor(mode, environments);
  return {
    plugins: [{ name: "acme-links", transformIndexHtml: (html: string) => fillLinks(html, links) }],
    build: {
      // Every asset is a file of its own: the security policy allows no data: font or script.
      assetsInlineLimit: 0,
      rollupOptions: {
        input: {
          index: fileURLToPath(new URL("index.html", import.meta.url)),
          notFound: fileURLToPath(new URL("404.html", import.meta.url)),
        },
      },
    },
    server: {
      port: 5174,
      // The page reads the portal's theme and the README's demo GIF.
      fs: { allow: [repoRoot] },
    },
    test: {
      environment: "node",
      include: ["src/**/*.test.ts"],
    },
  };
});
