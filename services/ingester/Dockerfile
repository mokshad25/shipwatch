# Dev image for the ingester. Build context is the repo root (uv workspace).
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
COPY services/ingester/pyproject.toml services/ingester/
RUN uv sync --frozen --no-dev --package ingester --no-install-workspace

COPY libs/common libs/common
COPY services/ingester services/ingester
RUN uv sync --frozen --no-dev --package ingester

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
CMD ["uvicorn", "ingester.main:app", "--host", "0.0.0.0", "--port", "8000"]
