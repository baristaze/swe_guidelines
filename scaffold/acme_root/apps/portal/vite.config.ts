/// <reference types="vitest/config" />
import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import pkg from "./package.json";

const repoRoot = fileURLToPath(new URL("../..", import.meta.url));

// The page calls the API on its own origin, as it does in the cloud, where
// the portal's distribution serves the API's paths. Here the dev server
// forwards them, the realtime socket included, to the API on the host.
// ACME_PORTAL_API_TARGET points it at another.
const apiProxy = {
  "/v1": {
    target: process.env.ACME_PORTAL_API_TARGET ?? "http://127.0.0.1:8000",
    ws: true,
  },
};

export default defineConfig({
  plugins: [react()],
  define: {
    __APP_VERSION__: JSON.stringify(`portal@${pkg.version}`),
  },
  server: {
    port: 5173,
    fs: { allow: [repoRoot] },
    proxy: apiProxy,
  },
  preview: {
    proxy: apiProxy,
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
