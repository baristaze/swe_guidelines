import * as Sentry from "@sentry/react";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { loadRuntimeConfig } from "./app/config";
import { initErrorReporting } from "./app/errors";
import { applyTheme } from "./app/themeModel";
import { usePreferencesStore } from "./store/preferences";
import "@fontsource-variable/inter";
import "./design/theme.css";
import "./design/kit.css";
import "./design/motion.css";

// The theme a person picked is on the page before anything paints, and a new pick follows.
applyTheme(document.documentElement, usePreferencesStore.getState().theme);
usePreferencesStore.subscribe((state) => applyTheme(document.documentElement, state.theme));

// The config comes first: the API client and error reporting are built from it,
// so the app is imported only once it is known.
const config = await loadRuntimeConfig();
initErrorReporting(config);
const { App } = await import("./app/App");

const root = document.getElementById("root");
if (root === null) throw new Error("index.html has no #root");
createRoot(root, {
  // React 19 hands uncaught and recoverable render errors to these hooks.
  onUncaughtError: Sentry.reactErrorHandler(),
  onRecoverableError: Sentry.reactErrorHandler(),
}).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
