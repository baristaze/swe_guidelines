# Two stages, a locked install of one workspace package, a non-root user,
# and a healthcheck on /healthz for the local stack. ECS reads the task
# definition's own health check, not this one; both probe liveness, and so
# does the load balancer's target group, because ECS replaces a task its
# target group calls unhealthy.
FROM python:3.14-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
COPY om/pyproject.toml om/
COPY infra/pyproject.toml infra/
COPY integrations/pyproject.toml integrations/
COPY services/api/pyproject.toml services/api/
# Every workspace member's manifest, so the frozen lock resolves; only the
# members this image runs are copied whole below.
COPY clients/python/pyproject.toml clients/python/
COPY apps/cli/pyproject.toml apps/cli/
RUN uv sync --frozen --no-dev --package acme-api --no-install-workspace
COPY om om
COPY infra infra
COPY integrations integrations
COPY services/api services/api
RUN uv sync --frozen --no-dev --package acme-api

FROM python:3.14-slim
RUN useradd --create-home --uid 10001 acme
WORKDIR /app
COPY --from=build --chown=acme:acme /app /app
COPY deployment/docker/entrypoint.sh /entrypoint.sh
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER acme
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s \
  CMD python -c "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status == 200 else 1)"
ENTRYPOINT ["/entrypoint.sh"]
CMD ["acme-api", "serve", "--host", "0.0.0.0", "--port", "8000"]
