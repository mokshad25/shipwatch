# Dev image for the worker. Build context is the repo root (uv workspace).
# Production hardening (multi-stage, non-root) comes in week 8.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

# Dependencies first so they cache independently of source changes.
COPY pyproject.toml uv.lock .python-version ./
COPY libs/common/pyproject.toml libs/common/
# Every workspace member's pyproject, or uv can't resolve the lockfile.
COPY services/ingester/pyproject.toml services/ingester/
COPY services/worker/pyproject.toml services/worker/
COPY services/demoapp/pyproject.toml services/demoapp/
RUN uv sync --frozen --no-dev --package worker --no-install-workspace

COPY libs/common libs/common
COPY services/worker services/worker
RUN uv sync --frozen --no-dev --package worker

ENV PATH="/app/.venv/bin:$PATH"
CMD ["worker"]
