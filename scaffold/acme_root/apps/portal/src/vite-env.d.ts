/// <reference types="vite/client" />

declare const __APP_VERSION__: string;

// Build variables; in the cloud /config.json replaces them (src/app/config.ts).
interface ImportMetaEnv {
  readonly VITE_API_URL?: string;
  readonly VITE_SENTRY_DSN?: string;
  readonly VITE_SENTRY_ENVIRONMENT?: string;
}
