# Shipwatch: Build Plan and Claude Code Prompts

Target role: **AI Engineer Intern, orqum.ai**
Schedule: 8 weeks, about 1 hour a day, Mon 26 Oct to Sun 20 Dec 2026

Shipwatch is a small version of what Orqum builds. It watches a GitHub repo and
production logs, uses an LLM agent to score risky PRs, traces incidents back to
the change that caused them, and reports to Slack and Linear.

---

## How to use this file

1. Save this file in the repo as `docs/BUILD_PLAN.md` and commit it.
2. Paste the **Master prompt** below into Claude Code once. Claude Code then
   plans the whole build and writes `CLAUDE.md`.
3. Each day, paste that day's prompt (for example **W1·D2**). Every prompt
   assumes Claude Code has read `CLAUDE.md` and `docs/BUILD_PLAN.md`.
4. Use **plan mode** (Shift+Tab) for the first prompt of each week. Approve
   the plan, then let it build.
5. Before you commit, read the diff. You need to be able to explain every line
   in an interview.
6. Write the commit message and the PR description yourself.

Rules for every session (these also go into `CLAUDE.md`):
- Do only the step asked. Don't build ahead.
- Every change ships with a test, and `make test` must pass before stopping.
- Never commit secrets. Keep a `.env.example` up to date instead.
- End each session with: what changed, how to run it, and what's next.

---

## Master prompt (run once, before W1·D1)

```
Read docs/BUILD_PLAN.md fully. This is an 8-week build of "Shipwatch", which I'm
doing to prove skills for an AI Engineer Intern role (Python, FastAPI, Kafka,
Postgres, Redis, MongoDB, LLM agents, evals, Prometheus/Grafana, Sentry,
React, Docker, Kubernetes, AWS, CI/CD).

Do NOT write application code yet. Instead:
1. Write CLAUDE.md at the repo root with: project goal (2 lines), architecture
   overview, the target repo layout from the plan, tech stack with versions,
   how to run / test / lint (via a Makefile), and the "Rules for every session"
   from the plan.
2. Write docs/ARCHITECTURE.md: a mermaid diagram of the final system (ingester
   -> Kafka -> worker/agent -> Postgres/Mongo -> Slack/Linear/web; logs ->
   logwatch -> anomalies -> root-cause) and one paragraph per component.
3. Point out anything in the plan that is risky, ambiguous or likely to take
   longer than 1 hour/day, and suggest how to cut it. Don't change the plan
   file; just tell me.
Then stop and wait for W1·D1.
```

### Target repo layout (for CLAUDE.md)

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

Kafka topics: `pr-events`, `pr-events.dlq`, `pr-verdicts`, `logs`, `deploys`, `anomalies`.

---

## Week 1: Webhook ingester
**Mon 26 Oct – Sun 1 Nov** · Proves: production-grade Python, REST APIs, testing, CI

**Done when:** opening a PR on your sandbox repo puts exactly one row in
Postgres, and CI is green.

### W1·D1: Scaffold
```
W1·D1. Scaffold the repo following the layout in CLAUDE.md, but only create
what week 1 needs: libs/common, services/ingester, docker-compose.yml (Postgres
16 + ingester), Makefile, pyproject (uv workspace), ruff, pytest.
- FastAPI app with GET /healthz returning {"status":"ok"}
- pydantic-settings config read from env; .env.example
- One passing test for /healthz
- .github/workflows/ci.yml running ruff + pytest on push and PR
- docs/SPEC_W1.md: POST /webhooks/github; HMAC-SHA256 check of
  X-Hub-Signature-256; store pull_request events; idempotent on
  X-GitHub-Delivery; 401 on bad signature, 202 on accept, 200 on duplicate.
Stop after the scaffold and show me `make up` and `make test`.
```

### W1·D2: Webhook endpoint and signature check
```
W1·D2. Implement POST /webhooks/github in services/ingester per docs/SPEC_W1.md.
- Verify X-Hub-Signature-256 with hmac.compare_digest using GITHUB_WEBHOOK_SECRET
- Read the raw body before parsing JSON (the signature is over raw bytes)
- Return 401 for a missing or bad signature, 202 for a valid one
- Don't store anything yet; just log event type and delivery id
Tests: valid signature, wrong signature, missing header, wrong secret.
Explain in 3 lines why compare_digest matters.
```

### W1·D3: Database models and migration
```
W1·D3. Add Postgres persistence in libs/common.
- SQLAlchemy 2.0 models: Repo(id, full_name), PullRequest(id, repo_id, number,
  title, author, state, head_sha, base_branch, created_at, merged_at),
  WebhookEvent(id, delivery_id, event_type, action, payload JSONB, received_at)
- Alembic set up in libs/common with the first migration; `make migrate`
- Ingester: on a pull_request event, upsert Repo and PullRequest and insert
  WebhookEvent in one transaction
Tests use a real Postgres via docker (pytest fixture that creates a fresh
schema), not SQLite.
```

### W1·D4: Idempotency
```
W1·D4. Make the ingester idempotent.
- Unique constraint on WebhookEvent.delivery_id (new migration)
- On a duplicate delivery, return 200 {"status":"duplicate"} and change nothing
- Handle the race: two identical deliveries arriving at once must still give
  exactly one row (catch IntegrityError rather than check-then-insert)
Tests: same delivery twice gives 1 row; concurrent duplicates (asyncio.gather)
give 1 row.
Add a short "Idempotency" section to docs/SPEC_W1.md explaining the approach.
```

### W1·D5: Real webhooks and fixtures
```
W1·D5. Connect to real GitHub.
- Add a `make tunnel` target that runs smee-client forwarding to the local
  ingester (SMEE_URL in .env)
- Write docs/SETUP_GITHUB.md: how to add the webhook to a sandbox repo (content
  type JSON, secret, "Pull requests" event only)
- I'll open, update and merge a PR and paste the payloads into
  services/ingester/tests/fixtures/. Then write tests that replay each
  fixture with a correct signature and check the DB state after each.
Wait for me to paste the payloads before writing the fixture tests.
```

### W1·D6: Edge cases and errors
```
W1·D6. Harden the ingester.
- Ignore non-pull_request events with 202 {"status":"ignored"} (but still
  record the delivery)
- 400 on malformed JSON; 413 if body > 5 MB
- Structured JSON logging (structlog) with delivery_id on every line
- Global exception handler: log with traceback, return 500 without leaking
  internals
Tests for each case. Run the whole suite and fix anything flaky.
```

### W1·D7: README and review
```
W1·D7. Wrap up week 1.
- README: what Shipwatch is (2 lines), architecture diagram (mermaid,
  week-1 parts highlighted), quickstart in under 8 commands, curl example
  that sends a signed request (include a small script scripts/sign_and_send.py),
  CI badge
- Review the whole ingester like a senior engineer: list issues by severity.
  Fix anything high severity; list the rest as TODOs in docs/SPEC_W1.md.
- Check the week-1 "Done when" and tell me if anything's missing.
```

---

## Week 2: Event pipeline that survives a crash
**Mon 2 Nov – Sun 8 Nov** · Proves: distributed systems basics, Kafka, queues, retries, idempotency, DLQs

**Done when:** the crash test passes and its numbers are written up.

### W2·D1: Kafka in the stack and producer
```
W2·D1 (plan mode). Add Redpanda (Kafka-compatible) to docker-compose plus
Redpanda Console. Create topics on startup: pr-events (3 partitions),
pr-events.dlq (1 partition).
- libs/common/kafka.py: typed producer wrapper (confluent-kafka), JSON with a
  pydantic envelope {event_id, delivery_id, type, occurred_at, payload}
- Ingester: after the DB commit, publish to pr-events keyed by repo full_name
  (so one repo's events stay ordered). Return 202 without waiting on downstream.
- What if the publish fails after the DB commit? Implement the transactional
  outbox pattern: write an outbox row in the same transaction, and a small
  relay loop publishes and marks it sent.
Write docs/SPEC_W2.md first, then implement.
```

### W2·D2: Consumer service
```
W2·D2. Create services/worker: a Kafka consumer on pr-events.
- Consumer group "worker"; enable.auto.commit=false
- Process each message (for now: log it and write a row to a
  processed_events table), then commit the offset only after success
- Graceful shutdown on SIGTERM: finish the current message, commit, close
- Add it to docker-compose
Tests: an integration test that produces 5 events and asserts 5 processed rows.
```

### W2·D3: Retries and dead-letter queue
```
W2·D3. Add retries and a DLQ to the worker.
- On a processing error, retry with exponential backoff and jitter (3 attempts)
- After the last failure, publish the original message plus error info
  (error type, message, attempts, first_failed_at) to pr-events.dlq, then
  commit
- Separate retryable errors (timeouts, connection) from non-retryable ones
  (validation); non-retryable go straight to the DLQ
- Add a FAIL_RATE env var to inject random failures for testing
Tests for both paths.
```

### W2·D4: Idempotent processing with Redis
```
W2·D4. Make processing idempotent.
- Add Redis to compose
- Before processing, SET event_id NX with a TTL of 7 days; if it already exists,
  skip and commit
- Mark "done" only after success: use a status key (processing -> done)
  so a crash mid-processing doesn't block a retry forever. Explain the
  trade-off in docs/SPEC_W2.md.
Tests: the same event delivered twice is processed once.
```

### W2·D5: DLQ replay tool
```
W2·D5. Write scripts/dlq_replay.py.
- List DLQ messages grouped by error type
- --replay [--error-type X] [--limit N] republishes them to pr-events
- --dry-run shows what would happen
Tests for the grouping and filtering logic. Document it in README "Operations".
```

### W2·D6: Crash test
```
W2·D6. Write scripts/crash_test.py and run it.
- Produce 1,000 events with unique ids
- After a random 2–5 s, kill the worker container (docker kill), wait,
  restart it
- When finished, count: produced, processed, duplicates (processed > once),
  lost (never processed), in DLQ
- Print a table and save it to docs/results/crash_test.md
Run it 3 times. If anything's lost or duplicated, find the bug and fix it.
```

### W2·D7: Write-up
```
W2·D7. Update docs/SPEC_W2.md with a "Delivery guarantees" section:
at-least-once delivery, effectively-once processing, what happens on each failure
(ingester crash, Kafka down, worker crash mid-message, poison message).
Include the crash test results. Update the README diagram. Then do a senior
review of week 2 and fix the high-severity issues.
```

---

## Week 3: PR risk agent
**Mon 9 Nov – Sun 15 Nov** · Proves: LLM/agentic AI basics, tool orchestration, structured output

Same pattern as StockGuard: the LLM gathers evidence and proposes risk factors,
and a deterministic rule decides the final score.

**Done when:** opening a PR on the sandbox repo stores a verdict along with its
tool-call trace.

### W3·D1: Tools
```
W3·D1 (plan mode). In services/worker/agent/, implement the agent's tools as plain,
tested Python functions (GitHub REST via httpx, GITHUB_TOKEN from env):
- get_diff(repo, pr_number) -> files changed, additions/deletions, patch
  (truncate large patches; say so in the output)
- file_churn(repo, paths, days=90) -> commits per file
- author_history(repo, author) -> merged PRs, reverted PRs by this author
- ci_status(repo, sha) -> check runs summary
Cache responses in Redis for 10 min. Tests with recorded responses (respx);
no live calls in tests. Write docs/SPEC_W3.md first.
```

### W3·D2: Agent loop
```
W3·D2. Build the agent loop with LangChain tool calling (Anthropic or OpenAI,
picked by LLM_PROVIDER env).
- System prompt: a senior reviewer looking for deployment risk; must cite
  evidence from tool output
- Cap at 6 tool calls; 60 s timeout overall
- Record a trace: each tool call, its args, a summary of the result, tokens,
  latency
Add a CLI: `python -m agent.run owner/repo 123` that prints the trace.
```

### W3·D3: Structured output
```
W3·D3. Make the agent return a Pydantic RiskAssessment:
factors: list[RiskFactor(kind: Literal[large_diff, hot_files, risky_paths,
new_author, failing_ci, missing_tests, migration, config_change], severity
1-3, evidence: str)], summary: str, confidence: float.
Use the provider's structured-output / tool-schema mode. On a parse failure,
retry once with the validation error in the prompt.
Tests with a fake LLM that returns good, bad and partial output.
```

### W3·D4: Deterministic score gate
```
W3·D4. Add agent/scoring.py: a pure function that turns a RiskAssessment and
raw diff stats into a score 0–100 and a level (low/medium/high). The LLM
never sets the score directly.
- Weighted factors, plus hard rules (e.g. a migration together with failing
  CI is always high)
- Weights in a YAML file so they can be tuned in week 5
Table-driven tests with at least 12 cases. In SPEC_W3.md, explain why the
score is deterministic.
```

### W3·D5: Run on real PRs
```
W3·D5. Pick a mid-sized public Python repo (suggest 3 options). Run the agent on
10 recent merged PRs, save each RiskAssessment + trace to
docs/results/w3_samples/, and write a summary table (PR, score, top factor,
tokens, latency, cost). Tell me which verdicts look wrong and why.
```

### W3·D6: Failure handling
```
W3·D6. Make the agent fail safely.
- LLM timeout, rate limit, tool error, malformed output -> verdict level
  "needs_review" with the reason; never crash the worker
- Retry rate limits with backoff (counting toward the worker's retry budget)
- Per-PR token budget; stop and return needs_review if it's exceeded
Tests for each failure using the fake LLM and fake tools.
```

### W3·D7: Wire into the pipeline
```
W3·D7. Connect the agent to the worker.
- On pull_request opened or synchronize: run the agent, store a Verdict
  (pr_id, head_sha, score, level, assessment JSONB, created_at) in Postgres
  (new migration), and the full trace in MongoDB (add Mongo to compose)
- Publish a summary to pr-verdicts
- Idempotency: one verdict per (pr_id, head_sha)
I'll open a PR on the sandbox repo to test end to end. Then do a week-3 review.
```

---

## Week 4: Root-cause explainer (builds on TREMOR)
**Mon 16 Nov – Sun 22 Nov** · Proves: production signals/logs, risk detection, root-cause explanation

**Done when:** the injected bug is named as the top cause, and you have it on
video. Send the Orqum follow-up this week.

### W4·D1: Demo app and log stream
```
W4·D1 (plan mode). Create services/demoapp: a small FastAPI "orders" API with
3 endpoints that writes structured JSON logs (with occasional normal warnings).
- A load script that hits it at about 20 req/s
- Ship logs to the Kafka topic `logs` (a log handler that produces to Kafka,
  or a sidecar reading stdout)
- A deploy marker: `make deploy-demo SHA=...` restarts demoapp and publishes
  {service, sha, pr_number, deployed_at} to `deploys`
Write docs/SPEC_W4.md first.
```

### W4·D2: Log template mining
```
W4·D2. Create services/logwatch consuming `logs`.
- Use drain3 (same approach as my TREMOR project) to mine templates; keep
  the drain3 state in Redis so a restart doesn't lose it
- Per template, keep per-minute counts in Redis (sorted sets or buckets)
- Expose the current templates and counts on GET /templates
Tests: feed sample log lines, check the templates and counts.
```

### W4·D3: Anomaly detection
```
W4·D3. Add anomaly detection to logwatch.
- New template: an ERROR or WARN template first seen in the last 10 min
- Rate spike: the current minute's count > mean + 3·std of the previous 30 min
  (minimum count threshold to avoid noise)
- Publish Anomaly {id, kind, template, sample_lines, window, service} to
  `anomalies`; de-duplicate repeats of the same anomaly for 15 min
Tests with synthetic count series for spike, no spike and new template.
```

### W4·D4: Correlation with deploys
```
W4·D4. Add root-cause candidate ranking.
- On an anomaly: find deploys of that service in the previous 60 min, and the
  PRs included in them
- Rank by: time since deploy, overlap between the PR's changed files and
  file paths or function names in stack traces or log lines, and the PR's
  verdict score from week 3
- Return the top 3 candidates with a score breakdown
Pure function plus table-driven tests.
```

### W4·D5: LLM explanation
```
W4·D5. Add an explainer that takes an anomaly and its top candidates and
writes a short incident note: what broke, the likely cause (PR #, the
specific diff hunk), confidence, and the suggested next step. It must quote
the log template and the diff lines it relies on; if the evidence is weak,
it says so. Store the Incident in Postgres, publish to `anomalies` with
the explanation attached, and print it from a CLI.
```

### W4·D6: Bug injection end to end
```
W4·D6. Write scripts/inject_bug.sh:
1. In the sandbox copy of demoapp, create a branch with a real bug (e.g. a
   None check removed in the discount calculation, which raises on some
   orders), open a PR, merge it
2. Deploy demoapp at that SHA (deploy marker)
3. Run load for 5 minutes
4. Print the incident note
Also run 2 decoy PRs (harmless changes) deployed just before, so ranking is
actually tested. Fix ranking until the real PR comes first.
```

### W4·D7: Demo and follow-up
```
W4·D7. Help me prepare the demo.
- Update the README with a "Root-cause demo" section: diagram, steps, sample
  incident note
- Write a 60-second demo script (what to show, in order)
- Draft a short follow-up message to Orqum (under 120 words) linking the repo
  and the video. I'll edit it before sending.
Then do the week-4 review.
```

---

## Week 5: Eval harness
**Mon 23 Nov – Sun 29 Nov** · Proves: evals, data pipelines, measuring an LLM system

**Done when:** `docs/EVALS.md` has real numbers, and a CI run blocks a bad
prompt change. This is a shorter week; use D7 to catch up.

### W5·D1: Dataset builder
```
W5·D1 (plan mode). Create evals/build_dataset.py.
- Find PRs in 2–3 public repos that were later reverted (search for
  "Revert \"...\"" commits/PRs) or followed by a hotfix within 7 days.
  Label these risky=1
- Sample the same number of merged PRs with no revert or hotfix in 30 days.
  Label these risky=0
- Target 20 + 20. Save as evals/data/prs.jsonl with repo, number, label and
  the reason for the label
Write docs/EVALS.md with the labeling method and its known biases.
```

### W5·D2: Eval runner
```
W5·D2. Create evals/run.py.
- Run the agent and scorer over the dataset (concurrency 4; cache agent output
  by (pr, prompt_version, model) so reruns are cheap)
- Metrics at the "high" threshold: precision, recall, F1, plus a confusion
  matrix, average tokens, cost per PR, p50/p95 latency
- Write evals/reports/<timestamp>.json and a markdown summary
`make eval` runs it.
```

### W5·D3: Compare prompts and models
```
W5·D3. Version the system prompt (prompts/v1.md, v2.md). Write v2 to fix the
weaknesses seen in the W3·D5 samples. Run the eval for {v1, v2} × {2 models}
and produce a comparison table in docs/EVALS.md (F1, recall, cost/PR, latency).
Recommend a default and explain why in 3 lines.
```

### W5·D4: Tune the scoring weights
```
W5·D4. Tune scoring.yaml weights on the dataset: a simple grid or
coordinate search on a 70/30 train/test split. Report the train vs test F1
so overfitting is visible. Only keep new weights if the test F1 improves.
Record the result in EVALS.md.
```

### W5·D5: CI regression gate
```
W5·D5. Add .github/workflows/eval.yml.
- Runs on PRs that touch prompts/, agent/ or scoring.yaml
- Runs a fixed 10-PR subset using cached tool outputs (only the LLM is live;
  use a repo secret for the key)
- Fails if F1 drops more than 5 points against evals/baseline.json
- Posts the metrics table as a PR comment
Then open a PR with a deliberately worse prompt to prove it blocks.
```

### W5·D6: Error analysis
```
W5·D6. Find the 5 worst misses (high-confidence false positives and false
negatives). For each: PR link, what the agent saw, why it was wrong, and
the fix category (missing tool, prompt, scoring, label noise). Add the
results to EVALS.md and create GitHub issues for the top 2 fixes.
```

---

## Week 6: Instrumentation
**Mon 30 Nov – Sun 6 Dec** · Proves: debugging, instrumentation, alerts, failure modes

**Done when:** every alert has fired once in a test, and the screenshots are in
the repo.

### W6·D1: Structured logs and correlation IDs
```
W6·D1 (plan mode). Standardize logging across all services via libs/common/logging.py
(structlog, JSON). Generate a correlation id at the ingester (delivery_id),
carry it in Kafka headers, and bind it in every consumer so one PR's journey
can be followed across services with a single grep. Add a test that checks
the header goes through the producer/consumer helpers.
```

### W6·D2: Prometheus metrics
```
W6·D2. Add Prometheus metrics (prometheus-client) and a /metrics endpoint
on every service:
- ingester: webhooks_total{event,status}, request latency histogram
- worker: events_processed_total{status}, processing_seconds, retries_total,
  dlq_published_total, agent_tokens_total, agent_cost_usd_total, llm_errors_total
- logwatch: lines_total, anomalies_total{kind}
- consumer lag per group/partition (exporter or Redpanda's own metrics)
Add Prometheus to compose with scrape config.
```

### W6·D3: Grafana dashboard
```
W6·D3. Add Grafana to compose with provisioning (datasource + dashboard
JSON committed under deploy/grafana/). Dashboard rows: Throughput, Errors,
Kafka lag and DLQ depth, Agent (latency p50/p95, tokens, cost per hour),
Anomalies. Keep it to about 10 panels that answer real questions. List the
questions in the README.
```

### W6·D4: Alert rules
```
W6·D4. Add Prometheus alert rules (deploy/prometheus/alerts.yml) and
Alertmanager posting to a Slack webhook (or a log receiver if Slack isn't set):
- ConsumerLagHigh (lag > 500 for 5m)
- DLQGrowing (dlq_published increase > 10 in 10m)
- LLMErrorRateHigh (> 10% for 5m)
- IngesterDown (up == 0 for 1m)
Each alert has summary, description and a runbook link to FAILURE_MODES.md.
Validate with promtool in CI.
```

### W6·D5: Sentry
```
W6·D5. Add the Sentry SDK to all Python services (DSN from env; disabled if
unset), with the correlation id as a tag and release = git SHA. Add a debug
endpoint (only when ENV=dev) that raises, to confirm events arrive. Make sure
no PR diff content or tokens are sent to Sentry (before_send scrubber + test).
```

### W6·D6: Failure modes document
```
W6·D6. Write docs/FAILURE_MODES.md: 8 ways Shipwatch breaks. For each: cause,
user impact, how we'd notice (which metric, alert or log), immediate fix,
long-term fix. Include: Kafka down, Postgres down, LLM provider outage,
GitHub rate limit, poison message, consumer lag from a slow agent, drain3
state loss, bad prompt deploy.
```

### W6·D7: Chaos drills
```
W6·D7. Write scripts/chaos.sh with one drill per alert (stop Redpanda, set
a bad LLM key, send poison messages, stop the ingester). Run each, confirm the
alert fires and recovery works, and record the result in FAILURE_MODES.md.
I'll take Grafana and alert screenshots and save them to docs/img/.
```

---

## Week 7: Slack, Linear and a review page
**Mon 7 Dec – Sun 13 Dec** · Proves: GitHub/Jira/Linear/Slack integrations, React, MongoDB, shipping and iterating with users

**Done when:** a real PR triggers a Slack message and a Linear issue, and you
have feedback from someone other than you.

### W7·D1: Slack notifier
```
W7·D1 (plan mode). Create services/notifier consuming pr-verdicts and anomalies.
- Slack (Block Kit via an incoming webhook or bot token): a PR risk card with
  score, level, top 3 factors with evidence, and links to the PR and the review
  page; an incident card with the explanation
- Only post when level >= threshold (env); de-duplicate per (pr, sha)
- Retries and DLQ reuse the libs/common helpers from week 2
Tests with a mocked Slack client.
```

### W7·D2: Linear integration
```
W7·D2. Add Linear (GraphQL API, key from env) to the notifier:
- High-risk PR -> create an issue in a "Shipwatch" team/project with
  the summary, factors and PR link; store the issue id on the Verdict
- Incident -> create an issue labeled "incident" linking the suspected PR
- Idempotent: never create two issues for the same verdict or incident
Tests with mocked GraphQL responses.
```

### W7·D3: Read API
```
W7·D3. Create services/api (FastAPI) for the web page:
GET /verdicts?repo=&level=&page=, GET /verdicts/{id} (includes the trace from
Mongo), GET /incidents, POST /verdicts/{id}/feedback {rating: up|down, reason}.
Feedback stored in MongoDB. Simple API-key auth via header. OpenAPI docs on.
Tests for every endpoint.
```

### W7·D4: React review page
```
W7·D4. Create web/ with Vite + React + TypeScript. Pages:
- Verdict list: repo filter, level chips, score, PR title, time
- Verdict detail: factors with evidence, a collapsible tool-call trace,
  thumbs up/down with a reason box
- Incidents list
Plain CSS or Tailwind; clean, not fancy. Typed API client generated from
OpenAPI. Add it to compose.
```

### W7·D5: Feedback into evals
```
W7·D5. Write evals/from_feedback.py: export thumbs-down verdicts (with reasons)
as candidate eval cases in evals/data/feedback_candidates.jsonl for manual
labeling, and print simple stats (feedback count, agreement rate by level).
Add a "Feedback loop" section to EVALS.md.
```

### W7·D6: Real users
```
W7·D6. Help me run a mini user test. Write:
- A 5-step guide for a friend to add Shipwatch's webhook to one of their repos
- 4 questions to ask them after they've used it
- A docs/USER_FEEDBACK.md template
I'll run it with 2–3 people and paste in their answers.
```

### W7·D7: Act on the feedback
```
W7·D7. Here's the feedback I collected: <paste>. Group it into themes, pick the
top 2 changes that fit in an hour, make them, and record "what we heard ->
what we changed" in USER_FEEDBACK.md. Then do the week-7 review.
```

---

## Week 8: Ship it
**Mon 14 Dec – Sun 20 Dec** · Proves: Docker, Kubernetes, AWS, CI/CD, async communication

**Done when:** the public URL is live, a merge to main deploys itself, and the
write-up is posted.

### W8·D1: Production images
```
W8·D1 (plan mode). Write production Dockerfiles for every service: multi-stage,
uv, non-root user, slim base, healthchecks. A web image served by nginx. A
`make build` that tags images with the git SHA. Report the image sizes
before and after.
```

### W8·D2: Kubernetes manifests
```
W8·D2. Write deploy/k8s/ with Kustomize (base + overlays/local + overlays/prod):
Deployments for each service with readiness/liveness probes on /healthz,
resource requests/limits, ConfigMap and Secret refs; Postgres, Redis, Mongo
and Redpanda as single-replica StatefulSets (fine for a demo; say so in the docs);
an Ingress for api, web and ingester. Test on a local k3d cluster and give me the
exact commands.
```

### W8·D3: AWS
```
W8·D3. Write docs/DEPLOY_AWS.md and scripts to run k3s on one EC2 instance
(t3.large or smaller; say what it costs per month): security group (80/443/22
from my IP only), k3s install, Traefik ingress with Let's Encrypt TLS for a
domain or nip.io, secrets created from a local .env (never committed). Make
everything repeatable with a single script. I'll run the AWS parts myself.
```

### W8·D4: Deploy pipeline
```
W8·D4. Add .github/workflows/deploy.yml: on merge to main, run tests, build
and push images to GHCR tagged with the SHA, then deploy to the EC2 k3s (SSH
with a deploy key stored as a secret, `kubectl set image` or kustomize
edit + apply), wait for rollout, and smoke-test /healthz. On failure, roll
back automatically. Document how to roll back by hand.
```

### W8·D5: README
```
W8·D5. Rewrite the README for a reviewer who has 5 minutes: one-line pitch,
demo GIF/video link, architecture diagram, "what this proves" (a short list
mapping features to skills), eval numbers, quickstart (≤10 commands),
links to SPEC_W*, EVALS, FAILURE_MODES, DESIGN. Remove anything stale.
```

### W8·D6: Design document
```
W8·D6. Write docs/DESIGN.md as an async design doc a team could review:
context, goals and non-goals, architecture, key decisions with alternatives
considered (Kafka vs a Redis queue, deterministic score vs LLM score,
Postgres + Mongo split, outbox pattern), risks, what I'd do next with
more time. Under 1,500 words. Ask me 3 questions before writing.
```

### W8·D7: Launch
```
W8·D7. Help me launch.
- Draft a blog/LinkedIn write-up (about 600 words): the problem, what I built,
  the 3 hardest problems and how I solved them, the eval and crash-test
  numbers, the link. My voice; no hype.
- Write 8 resume bullets, one per week, each with a concrete number from
  docs/results or EVALS.md and a link to proof in the repo
- Run a final check of every week's "Done when" and list anything incomplete.
```

---

## If you fall behind

- Skip in this order: W7·D6–D7 (user test), W8·D3–D4 (AWS; demo on local k3d
  instead), W5·D4 (weight tuning), W6·D5 (Sentry).
- Never skip: W2·D6 (crash test), W4·D6 (bug injection demo), W5·D2 (eval
  runner). These are the three results that make the project stand out.
- Losing a day is fine. Don't start the next week until the current week's "Done
  when" is true.
