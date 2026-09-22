# The container runtime: the same subject, in a box that starts empty.
#
# Build it from this folder:
#
#     docker build -t swe-guidelines-benchmark:latest -f benchmark/runtime/Dockerfile benchmark/runtime
#
# `benchmark/run.py --runtime container --build` runs that command for you.
# The image carries the runtimes the guideline names as current, the uv
# workspace tool, and the Claude Code command line, and nothing else. The
# run mounts the staged plugin payload read-only at /plugin, the target, when
# there is one, read-only at /target, and the workspace read-write at
# /workspace, and it hands the subject those paths, never the host's.
FROM python:3.14-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    NODE_MAJOR=24 \
    HOME=/home/runner \
    PATH=/home/runner/.local/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl git gnupg passwd ripgrep \
 && mkdir -p /etc/apt/keyrings \
 && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
 && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_${NODE_MAJOR}.x nodistro main" > /etc/apt/sources.list.d/nodesource.list \
 && apt-get update && apt-get install -y --no-install-recommends nodejs \
 && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --shell /bin/bash runner
USER runner
WORKDIR /workspace

# Pinned, and no script piped into a shell: uv comes from its release
# image, Claude Code at the version the benchmark workflow installs.
COPY --from=ghcr.io/astral-sh/uv:0.12.17 /uv /uvx /usr/local/bin/
RUN npm config set prefix /home/runner/.local && npm install -g @anthropic-ai/claude-code@2.1.276

ENTRYPOINT []
CMD ["bash", "-lc", "claude --version"]
