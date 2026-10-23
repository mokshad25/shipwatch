# Shipwatch

Shipwatch watches a GitHub repo and production logs, scores risky PRs with an LLM agent
(evidence from tools, deterministic final score), and traces incidents back to the change that caused them.
It reports to Slack, Linear and a small React review page. Full 8-week plan: `docs/BUILD_PLAN.md`.

## Architecture overview

GitHub webhooks hit the **ingester** (FastAPI), which verifies the HMAC signature, stores the event in
Postgres idempotently and (from week 2) publishes it to Kafka via a transactional outbox. The **worker**
consumes `pr-events`, runs the PR risk agent (LLM + GitHub tools) and stores a verdict (Postgres) plus its
trace (MongoDB), publishing to `pr-verdicts`. **logwatch** mines log templates from `logs` (drain3),
detects anomalies and correlates them with `deploys` to rank the PRs that likely caused them. The
**notifier** posts to Slack/Linear, and **api** + **web** serve the review page. Prometheus/Grafana,
alerts and Sentry cover operations. Details and diagram: `docs/ARCHITECTURE.md`.

Kafka topics: `pr-events`, `pr-events.dlq`, `pr-verdicts`, `logs`, `deploys`, `anomalies`.

## Repo layout (target; create pieces only when their week needs them)

```
shipwatch/
├── CLAUDE.md
├── Makefile                 # up, down, test, lint, fmt, migrate, eval
├── docker-compose.yml
├── .env.example
├── .github/workflows/       # ci.yml, eval.yml, deploy.yml
├── docs/                    # BUILD_PLAN, ARCHITECTURE, SPEC_W*, EVALS, FAILURE_MODES, DESIGN
├── libs/common/             # shared: settings, db models, schemas, kafka helpers, logging
├── services/
│   ├── ingester/            # FastAPI: GitHub webhooks -> Postgres + Kafka
│   ├── worker/              # Kafka consumer: runs the PR risk agent
│   ├── logwatch/            # Kafka consumer: drain3 log mining + anomaly detection
│   ├── notifier/            # Slack + Linear output
│   ├── api/                 # FastAPI read API for the web page
│   └── demoapp/             # tiny app that emits logs, used to inject bugs
├── web/                     # React (Vite) review page
├── evals/                   # dataset, runner, reports
└── deploy/k8s/              # manifests for k3s
```

Python packages live under `src/`: `libs/common/src/shipwatch_common`, `services/<name>/src/<name>`.
Each service keeps its tests in `services/<name>/tests/`. The repo is a **uv workspace** (root `pyproject.toml`).

## Tech stack

| Area | Choice |
|---|---|
| Language / tooling | Python 3.12, uv (workspace), ruff (lint + format), pytest 8 + pytest-asyncio |
| Web | FastAPI 0.115+, uvicorn, httpx (tests + GitHub client) |
| Config | pydantic 2, pydantic-settings 2 (env / `.env`) |
| Storage | Postgres 16, SQLAlchemy 2.x (async, asyncpg), Alembic; Redis 7 (wk2); MongoDB 7 (wk3) |
| Messaging | Redpanda (Kafka API) + confluent-kafka (wk2) |
| LLM | LangChain tool calling, Anthropic or OpenAI via `LLM_PROVIDER` (wk3) |
| Logs / ML | structlog JSON logs (wk1), drain3 (wk4) |
| Ops | prometheus-client, Prometheus, Grafana, Alertmanager, Sentry (wk6) |
| Frontend | React + Vite + TypeScript (wk7) |
| Deploy | Docker (multi-stage, uv), Kustomize, k3s/k3d, AWS EC2, GitHub Actions + GHCR (wk8) |

## Run / test / lint

```
cp .env.example .env     # then set GITHUB_WEBHOOK_SECRET
make up                  # docker compose up --build -d (Postgres + ingester on :8000)
make migrate             # alembic upgrade head
make test                # starts Postgres if needed, runs pytest against real Postgres
make lint                # ruff check + ruff format --check
make fmt                 # ruff format + ruff check --fix
make down                # stop everything
```

Tests never use SQLite; they run against the compose Postgres (CI uses a `postgres:16` service container).

## Rules for every session

- Do only the step asked. Don't build ahead.
- Every change ships with a test, and `make test` must pass before stopping.
- Never commit secrets. Keep a `.env.example` up to date instead.
- End each session with: what changed, how to run it, and what's next.
- The user writes commit messages and PR descriptions; don't commit unless asked.
