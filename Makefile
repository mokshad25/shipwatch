.PHONY: up down logs db test lint fmt migrate forward capture sandbox-pr

# Where `make forward` delivers: 8000 = ingester, 8001 = capture relay (`make forward FORWARD_PORT=8001`).
FORWARD_PORT ?= 8000

up: .env ## Build and start Postgres + ingester, wait until healthy
	docker compose up --build -d --wait

down: ## Stop all containers (data volume is kept)
	docker compose down

logs: ## Follow ingester logs
	docker compose logs -f ingester

db: ## Start only Postgres and wait until it accepts connections
	docker compose up -d --wait postgres

test: db ## Run the test suite against the compose Postgres
	uv run pytest

lint: ## Lint and check formatting
	uv run ruff check .
	uv run ruff format --check .

fmt: ## Format and autofix
	uv run ruff format .
	uv run ruff check --fix .

migrate: db ## Apply database migrations
	uv run alembic -c libs/common/alembic.ini upgrade head

forward: .env ## Forward this repo's pull_request webhooks to the local ingester (gh-webhook)
	@set -a; . ./.env; set +a; \
	if [ -z "$${GITHUB_REPO:-}" ] || [ "$$GITHUB_REPO" = "your-user/your-repo" ]; then \
		echo "Set GITHUB_REPO in .env" >&2; exit 1; fi; \
	if [ -z "$${GITHUB_WEBHOOK_SECRET:-}" ] || [ "$$GITHUB_WEBHOOK_SECRET" = "change-me" ]; then \
		echo "Set GITHUB_WEBHOOK_SECRET in .env" >&2; exit 1; fi; \
	gh webhook forward --repo="$$GITHUB_REPO" --events=pull_request \
		--url="http://localhost:$(FORWARD_PORT)/webhooks/github" --secret="$$GITHUB_WEBHOOK_SECRET"

capture: ## Save each forwarded delivery as a test fixture and relay it to the ingester (:8001)
	uv run python scripts/capture_webhooks.py

sandbox-pr: .env ## Open, update, close, reopen and merge a throwaway PR against `sandbox`
	./scripts/sandbox_pr.sh

.env:
	cp .env.example .env
