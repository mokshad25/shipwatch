.PHONY: up down logs db test lint fmt migrate

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

.env:
	cp .env.example .env
