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

1. Read the raw body bytes. Nothing is parsed before the signature check: the signature is over the
   exact bytes, and re-serialised JSON would not match.
2. Compute `HMAC-SHA256(secret, body)` and compare it to the header with `hmac.compare_digest`
   (constant time). Missing header, wrong prefix or mismatch → **401**.
3. Missing `X-GitHub-Delivery` → **400**.
4. Parse JSON.
5. If `X-GitHub-Event` is `pull_request`: in **one transaction**, insert a `webhook_events` row and upsert
   the `repos` and `pull_requests` rows from the payload. Other event types are accepted and not stored
   (week 1 D6 revisits this).
6. If the delivery id was already stored → **200** `{"status":"duplicate"}`, nothing changes.

### Responses

| Status | Body | When |
|---|---|---|
| 202 | `{"status":"accepted"}` | Valid signature, new delivery. |
| 200 | `{"status":"duplicate"}` | Valid signature, delivery id already stored. |
| 400 | `{"detail": ...}` | Missing `X-GitHub-Delivery`. |
| 401 | `{"detail":"invalid signature"}` | Missing or bad signature. |

202 (not 201/200) because acceptance means "stored, processing happens later" (Kafka from week 2).

## Stored data

- `repos(id, full_name)`: `id` is GitHub's repository id.
- `pull_requests(id, repo_id, number, title, author, state, head_sha, base_branch, created_at, merged_at)`:
  `id` is GitHub's PR id; `(repo_id, number)` unique; upserted so the latest delivery wins.
- `webhook_events(id, delivery_id, event_type, action, payload JSONB, received_at)`: one row per delivery;
  the full payload is kept for replay and debugging.

## Configuration

| Env var | Required | Default |
|---|---|---|
| `GITHUB_WEBHOOK_SECRET` | yes | none (the app refuses to start) |
| `DATABASE_URL` | no | `postgresql+asyncpg://shipwatch:shipwatch@localhost:5432/shipwatch` |
| `LOG_LEVEL` | no | `INFO` |
| `ENV` | no | `dev` |

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
