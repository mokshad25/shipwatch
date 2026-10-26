# Dev image for demoapp. Build context is a repo tree: the checkout, or the `git archive` of the
# commit being deployed (scripts/deploy_demo.py).
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
RUN uv sync --frozen --no-dev --package demoapp --no-install-workspace

COPY libs/common libs/common
COPY services/demoapp services/demoapp
RUN uv sync --frozen --no-dev --package demoapp

# The deployed commit; every log line carries it as `sha`. Last, so a new SHA reuses the layers above.
ARG GIT_SHA=dev
ENV GIT_SHA=$GIT_SHA \
    PATH="/app/.venv/bin:$PATH"
EXPOSE 8100
# Access logging comes from our middleware (JSON, with request_id), so uvicorn's is off.
CMD ["uvicorn", "demoapp.main:app", "--factory", "--host", "0.0.0.0", "--port", "8100", "--no-access-log"]
