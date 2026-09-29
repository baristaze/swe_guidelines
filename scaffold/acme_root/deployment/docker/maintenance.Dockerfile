# Same shape as a service image, no exposed port. The healthcheck asks the
# serving process's /healthz on the metrics port, which answers from the
# loop's own last beat in memory; nothing boots per probe.
FROM python:3.14-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
COPY om/pyproject.toml om/
COPY infra/pyproject.toml infra/
COPY integrations/pyproject.toml integrations/
COPY workers/maintenance/pyproject.toml workers/maintenance/
# Every workspace member's manifest, so the frozen lock resolves; only the
# members this image runs are copied whole below.
COPY clients/python/pyproject.toml clients/python/
COPY apps/cli/pyproject.toml apps/cli/
RUN uv sync --frozen --no-dev --package acme-maintenance --no-install-workspace
COPY om om
COPY infra infra
COPY integrations integrations
COPY workers/maintenance workers/maintenance
RUN uv sync --frozen --no-dev --package acme-maintenance

FROM python:3.14-slim
RUN useradd --create-home --uid 10001 acme
WORKDIR /app
COPY --from=build --chown=acme:acme /app /app
COPY deployment/docker/entrypoint.sh /entrypoint.sh
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER acme
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s \
  CMD python -c "import os, urllib.request, sys; port = os.environ.get('ACME_METRICS_PORT', '9464'); sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:%s/healthz' % port).status == 200 else 1)"
ENTRYPOINT ["/entrypoint.sh"]
CMD ["acme-maintenance", "serve"]
