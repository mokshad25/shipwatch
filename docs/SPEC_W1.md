# SPEC W1: Webhook ingester

**Done when:** opening a PR on the sandbox repo puts exactly one row in Postgres, and CI is green.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/healthz` | Liveness. Always `200 {"status":"ok"}`. |
| POST | `/webhooks/github` | Receives GitHub webhook deliveries. |

## POST /webhooks/github

### Request headers (set by GitHub)

| Header | Use |
|---|---|
| `X-Hub-Signature-256` | `sha256=<hex>`: HMAC-SHA256 of the **raw request body** keyed with `GITHUB_WEBHOOK_SECRET`. |
| `X-GitHub-Event` | Event type, e.g. `pull_request`, `ping`. |
| `X-GitHub-Delivery` | Unique GUID per delivery. GitHub reuses it on manual "Redeliver". Idempotency key. |

### Processing order

1. Read the raw body bytes, at most `MAX_BODY_BYTES` (5 MB). A larger `Content-Length`, or a streamed
   body that grows past the limit, → **413** without buffering the rest.
2. Compute `HMAC-SHA256(secret, body)` and compare it to the header with `hmac.compare_digest`
   (constant time). Missing header, wrong prefix or mismatch → **401**. Nothing is parsed before this:
   the signature is over the exact bytes, and re-serialised JSON would not match.
3. Missing `X-GitHub-Delivery` or `X-GitHub-Event` → **400**.
4. Parse JSON. Invalid JSON, invalid UTF-8, or anything but a JSON object → **400**.
5. In **one transaction**: insert a `webhook_events` row for every event type. For `pull_request`
   events, also upsert the `repos` and `pull_requests` rows. Other event types are recorded and answered
   with `{"status":"ignored"}`.
6. If the delivery id was already stored → **200** `{"status":"duplicate"}`, nothing changes.
7. Any unexpected error → the transaction rolls back, the traceback is logged, and the client gets
   **500** `{"detail":"internal server error"}` with no internals.

### Responses

| Status | Body | When |
|---|---|---|
| 202 | `{"status":"accepted"}` | Valid `pull_request` delivery, stored. |
| 202 | `{"status":"ignored"}` | Valid delivery of another event type; recorded in `webhook_events` only. |
| 200 | `{"status":"duplicate"}` | Delivery id already stored. |
| 400 | `{"detail": ...}` | Missing `X-GitHub-Delivery`/`X-GitHub-Event`, or malformed JSON. |
| 401 | `{"detail":"invalid signature"}` | Missing or bad signature. |
| 413 | `{"detail":"payload too large"}` | Body over 5 MB. |
| 500 | `{"detail":"internal server error"}` | Unexpected error (details only in the logs). |

## Logging

One JSON object per line on stdout (structlog; stdlib and uvicorn records go through the same
formatter). Every line logged while handling a request carries `delivery_id` and `github_event`, so
`docker compose logs ingester | grep <delivery_id>` shows a delivery's whole story. Each request ends
with a `"request"` line (method, path, status, duration_ms); errors include a structured traceback.

202 (not 201/200) because acceptance means "stored, processing happens later" (Kafka from week 2).

## Stored data

- `repos(id, full_name)`: `id` is GitHub's repository id.
- `pull_requests(id, repo_id, number, title, author, state, head_sha, base_branch, created_at, merged_at,
  updated_at)`: `id` is GitHub's PR id; `(repo_id, number)` unique. Upserted only when the payload's
  `pull_request.updated_at` is not older than the stored one, because GitHub doesn't guarantee delivery
  order: a late, stale delivery is still recorded in `webhook_events` but can't roll the PR back.
- `webhook_events(id, delivery_id, event_type, action, payload JSONB, received_at)`: one row per delivery;
  the full payload is kept for replay and debugging.

## Configuration

| Env var | Required | Default |
|---|---|---|
| `GITHUB_WEBHOOK_SECRET` | yes | none; the app refuses to start if unset, under 16 chars, or `change-me` |
| `DATABASE_URL` | no | `postgresql+asyncpg://shipwatch:shipwatch@localhost:5432/shipwatch` |
| `LOG_LEVEL` | no | `INFO` |
| `ENV` | no | `dev` |
| `MAX_BODY_BYTES` | no | `5242880` (5 MB) |

## Idempotency

GitHub delivers at least once: it retries on timeouts, and "Redeliver" in the UI resends the same
`X-GitHub-Delivery`. Each delivery must change the database at most once.

- **Key:** `X-GitHub-Delivery`, stored in `webhook_events.delivery_id` with a unique constraint
  (`uq_webhook_events_delivery_id`, migration 0002).
- **Order inside the transaction:** the `webhook_events` row is inserted *first*, then the repo and PR
  are upserted. A duplicate fails on that first insert, the transaction rolls back, and the endpoint
  returns `200 {"status":"duplicate"}` with nothing changed (not even with a different payload).
- **No check-then-insert.** `SELECT ... WHERE delivery_id = ?` followed by `INSERT` has a race: two
  identical deliveries can both see "not there" and both insert. Instead we always insert and let
  Postgres decide. If two transactions insert the same key at once, the second one blocks on the first's
  uncommitted row; when the first commits it gets a unique violation (`IntegrityError`), which we map to
  "duplicate". Only an `IntegrityError` naming that constraint is treated as a duplicate; anything else
  is re-raised.
- **Tests:** same delivery twice → one row; a replay with different content changes nothing; five
  concurrent identical deliveries (`asyncio.gather`) → exactly one 202, four 200s, one row.

## Review (W1·D7) and TODOs

Senior review of the ingester at the end of week 1. High-severity items were fixed; the rest are TODOs.

### Fixed

| Sev | Issue | Fix |
|---|---|---|
| High | Out-of-order deliveries (GitHub doesn't guarantee order) overwrote newer PR state with older data, e.g. a late `synchronize` undoing a merge. | `pull_requests.updated_at` (migration 0003, backfilled from the newest stored payload); the upsert only applies when the incoming `updated_at` is not older. Test: stale delivery after a merge leaves the PR merged. |
| High | An empty or placeholder `GITHUB_WEBHOOK_SECRET` was accepted, making signatures trivially forgeable. | Settings validation: at least 16 characters and not `change-me`; the service refuses to start otherwise. |
| Medium | `conftest` used `setdefault` for the secret, so tests would sign with a real secret exported in the shell. | Tests always set their own throwaway secret. |

### TODO

| Sev | Issue | Suggested fix |
|---|---|---|
| Medium | If Postgres is down the endpoint returns 500, and GitHub does **not** retry failed deliveries automatically, so the event is lost until someone redelivers it. The week-2 outbox doesn't cover this (it needs the DB). | Periodic reconcile job: list the hook's failed deliveries via the GitHub API and redeliver them. |
| Medium | A signed `pull_request` payload missing expected fields (`head`, `user`, ...) gives a 500 rather than a 4xx. | Validate with a Pydantic model; record the delivery and return 422. |
| Medium | Postgres JSONB rejects `\u0000` in strings (and NaN), so such a payload fails with 500 on every redelivery. | Strip/escape NULs before storing, or store the raw body as text alongside. |
| Medium | Unauthenticated callers can make the ingester buffer up to 5 MB before the HMAC check; there's no rate limiting. | Body/connection limits and rate limiting at the proxy/ingress (week 8). |
| Medium | `/healthz` doesn't check the database, and the app starts even if migrations haven't run. | Add `/readyz` with a DB ping and schema-version check; use it as the readiness probe (week 8). |
| Medium | `make forward` passes the secret as `--secret`, visible in the process list while it runs (dev only). | Accept the risk for local dev, or wrap `gh` to read it from a file/stdin if supported. |
| Low | `updated_at` has 1-second resolution; two out-of-order deliveries within the same second resolve to whichever is processed last. | Tie-break with the event action order, or re-fetch the PR from the API on ties. |
| Low | Duplicate detection matches the constraint name in the error text. | Use asyncpg's `constraint_name` on the original exception. |
| Low | `delivery_id`/`event_type` are `VARCHAR(64)`; an oversized header after a valid signature gives a 500. | Validate header lengths up front (400). |
| Low | `/docs` and `/openapi.json` are public. | Disable outside `ENV=dev`. |
| Low | Dev Docker image is single-stage and runs as root. | Multi-stage, non-root image (week 8). |
| Low | Every event type's full payload is stored with no retention if the hook subscribes to `*`. | Retention policy, or store only headers for ignored events. |
| Low | CI doesn't build the Docker image. | Add a `docker build` job. |
| Low | `pull_requests.state` is `open`/`closed` only; consumers must check `merged_at` to tell a merge. | Add a derived `merged` flag or view if consumers need it. |
