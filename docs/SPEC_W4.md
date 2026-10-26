# SPEC W4: Root-cause explainer

**Done when:** the injected bug is named as the top cause, and it is on video.

A small "orders" service (`demoapp`) streams its logs to Kafka and announces every deploy. On D6 a real
bug is merged and deployed, and logwatch (D2–D5) has to name the PR that caused it. This spec grows one
section per day.

## D1: Demo app and log stream

### Topics

Created by `redpanda-init` alongside the week-2 topics.

| Topic | Partitions | Key | Producer | Consumer |
|---|---|---|---|---|
| `logs` | 3 | service name | demoapp (in-process log handler) | logwatch (D2) |
| `deploys` | 1 | service name | `scripts/deploy_demo.py` | logwatch (D4) |
| `anomalies` | 1 | service name | logwatch (D3) | notifier (later) |

Keying by service keeps one service's lines in order on one partition. `deploys` has a single partition
so every consumer sees deploys in the order they happened.

### demoapp

FastAPI app in `services/demoapp`, on <http://localhost:8100> when deployed. State is in memory (a fixed
product catalog and an orders dict), so a deploy starts from a clean slate.

| Endpoint | Result | Normal warnings |
|---|---|---|
| `GET /products` | 200, the catalog | – |
| `POST /orders` `{items: [{product_id, qty}], discount_code?}` | 201 order with `subtotal`, `discount`, `total` | `unknown discount code` (order still placed, no discount), `out of stock` (409) |
| `GET /orders/{id}` | 200 / 404 | `order not found` (404) |
| `GET /healthz` | 200 | – |

Any request slower than `SLOW_MS` (250 ms; handlers add a little random latency so this happens now and
then) also logs `slow request`. Unknown products and bad bodies are 422 and log nothing beyond the
request line. Unhandled exceptions become a generic 500 and an `ERROR` line with the traceback.

Pricing lives in `demoapp/pricing.py`. `apply_discount(subtotal, code)` explicitly handles `code=None`;
that check is what D6 removes.

### Log lines

Every line is one JSON object, rendered by `shipwatch_common.logging.json_formatter()`. The same line
goes to stdout and to the `logs` topic.

| Field | Always | Meaning |
|---|---|---|
| `timestamp` | yes | ISO 8601 UTC. |
| `level` | yes | `debug`, `info`, `warning`, `error`, `critical`. |
| `event` | yes | The message, e.g. `request`, `unknown discount code`. Variable parts go in other fields. |
| `logger` | yes | Python logger name, e.g. `demoapp.main`. |
| `service`, `sha` | yes | Which service and which deployed commit wrote it. |
| `request_id` | in a request | 12 hex chars, also returned as `X-Request-ID`. |
| `method`, `path`, `status`, `duration_ms` | `request` lines | One per request. |
| `exception` | on errors | structlog `dict_tracebacks`: a list of `{exc_type, exc_value, frames: [{filename, lineno, name, …}]}`. |

`frames[].filename` is the absolute path in the container, `/app/services/demoapp/src/demoapp/…`. Its
suffix after `/app/` is the repo path, which is what D4 matches against a PR's changed files;
`frames[].name` is the function name.

```json
{"event": "unknown discount code", "code": "SAVE99", "level": "warning", "logger": "demoapp.main",
 "timestamp": "2026-11-16T10:00:00.123456Z", "service": "demoapp", "sha": "3f2c…", "request_id": "a1b2c3d4e5f6"}
```

### Shipping logs

`demoapp.logship.KafkaLogHandler` is a `logging.Handler` attached next to the stdout handler. It
produces each formatted line to `logs` with the service name as key. It never blocks or raises in the
request path:

- `acks=1`, no idempotence (a duplicated or lost log line is acceptable; a slow request is not).
- If librdkafka's local queue is full, or the broker is down long enough for the queue to fill, the line
  is dropped and `dropped` is incremented.
- Records from the `rdkafka` logger are not shipped, so broker trouble can't feed itself.
- Pending lines are flushed for up to 5 s on shutdown.

### Deploy marker

```
make deploy-demo SHA=<commit-ish> [PR=<number>]
```

`scripts/deploy_demo.py`:

1. Resolves `SHA` to a full commit with `git rev-parse`.
2. Exports that commit with `git archive` into `.deploy/demoapp-src/` (gitignored).
3. Runs `docker compose --profile demo up -d --build --wait demoapp` with `DEMOAPP_CONTEXT` pointing
   at that export and `DEMOAPP_SHA` set, so the container really runs that commit's code and its logs
   carry its `sha`.
4. Finds the PR: `PR` if given, else the first merged PR GitHub associates with the commit
   (`gh api repos/{GITHUB_REPO}/commits/{sha}/pulls`), else `null`.
5. Only after the container is healthy, publishes a `Deploy` to `deploys` and waits for the ack (exit
   code 1 if it fails).

`Deploy` (`shipwatch_common.schemas`), UTF-8 JSON:

| Field | Type | Meaning |
|---|---|---|
| `deploy_id` | UUID | Generated per deploy. |
| `service` | str | `demoapp`. |
| `repo` | str | `GITHUB_REPO`, where the PR lives. |
| `sha` | str | Full commit SHA that is now running. |
| `pr_number` | int \| null | The PR that commit came from. |
| `deployed_at` | datetime (UTC) | When the new container became healthy. |

demoapp is in the compose profile `demo`, so `make up` neither builds nor restarts it; only
`make deploy-demo` does.

### Load

```
make load-demo [RATE=20] [DURATION=60]
```

`demoapp-load` is open loop: request *i* is sent at `start + i/RATE` whether or not earlier ones have
answered, so a slow server can't lower the offered rate. Mix:

| Share | Request |
|---|---|
| 60% | `POST /orders`, 1–3 items. Of these ≈10% use a valid discount code, ≈3% an unknown one, ≈2% a quantity that is out of stock. |
| 25% | `GET /orders/{id}` of an order this run created |
| 10% | `GET /products` |
| 5% | `GET /orders/{id}` that doesn't exist |

At 20 req/s that is roughly 0.4 unknown-code, 0.2 out-of-stock and 1 not-found warning per second: a
steady background of normal warnings and no errors. It prints status counts and the achieved rate at
the end. `--seed` makes the mix reproducible.
