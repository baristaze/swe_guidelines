# Two stages: the pnpm workspace builds the portal bundle, and an
# unprivileged nginx serves it and forwards the API's paths to the api
# container. VITE_API_URL and VITE_SENTRY_DSN are compiled into the bundle,
# so they are build arguments naming what the browser reaches; an empty
# VITE_API_URL means the page's own origin, and an empty VITE_SENTRY_DSN
# leaves error reporting off.
FROM node:24.21.0-alpine AS build
RUN corepack enable
WORKDIR /app
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml tsconfig.base.json eslint.config.js ./
COPY apps/portal/package.json apps/portal/
COPY clients/typescript/package.json clients/typescript/
RUN pnpm install --frozen-lockfile --filter @acme/portal...
COPY clients/typescript clients/typescript
COPY apps/portal apps/portal
COPY deployment/realtime-timeouts.json deployment/
ARG VITE_API_URL=
ARG VITE_SENTRY_DSN=
ARG VITE_SENTRY_ENVIRONMENT=local
RUN pnpm --filter @acme/portal build

FROM nginxinc/nginx-unprivileged:1.30-alpine-slim
COPY deployment/docker/portal.nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/apps/portal/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s \
  CMD wget -q -O /dev/null http://127.0.0.1:8080/ || exit 1
