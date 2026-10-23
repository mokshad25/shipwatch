# Connecting real GitHub webhooks (W1·D5)

GitHub's `pull_request` webhooks go to the local ingester through `gh webhook forward`. Test PRs only
ever target the long-lived **`sandbox`** branch of this repo, never `main`.

## One-time setup

1. Install the GitHub CLI extension and give the token permission to create webhooks:
   ```bash
   gh extension install cli/gh-webhook
   gh auth refresh -s admin:repo_hook
   ```
2. Make sure `sandbox` exists on GitHub. To create it from `main`:
   ```bash
   git fetch origin && git push origin origin/main:refs/heads/sandbox
   ```
3. Fill in `.env` (copied from `.env.example`; never committed):
   ```
   GITHUB_REPO=<owner>/<repo>              # e.g. mokshad25/shipwatch
   GITHUB_WEBHOOK_SECRET=<random hex>      # python -c "import secrets; print(secrets.token_hex(32))"
   ```
   Restart the ingester after changing the secret (`make up`) so both sides use the same value.

## Run it

Three terminals:

```bash
make up && make migrate     # 1. Postgres + ingester on :8000, schema up to date
make forward                # 2. creates a temporary webhook (content type JSON, "Pull requests" only,
                            #    your secret) and forwards deliveries to localhost:8000
make sandbox-pr             # 3. one PR against `sandbox`: opened, synchronize, edited,
                            #    closed, reopened, merged (squash) and branch deleted
```

`gh webhook forward` removes its webhook when you stop it (Ctrl-C). Only one forwarder per repo can run at a time.

`scripts/sandbox_pr.sh` refuses to run if the base would be `main`/`master` or the repo's default branch,
works in a temporary git worktree (your checkout isn't touched), and checks the PR's base after creating it.

## Check what landed in Postgres

```bash
docker compose exec postgres psql -U shipwatch -c \
  "select delivery_id, event_type, action, received_at from webhook_events order by id;"
docker compose exec postgres psql -U shipwatch -c \
  "select number, title, state, head_sha, base_branch, merged_at from pull_requests;"
```

One `make sandbox-pr` run gives 6 `webhook_events` rows and 1 `pull_requests` row, with
`merged_at` set and `state = closed`. `make logs` shows each delivery being accepted.

## Capturing test fixtures

The fixture tests replay real payloads from `services/ingester/tests/fixtures/`. To record them:

```bash
make capture                     # terminal 1: relay on :8001 that saves fixtures, then forwards to :8000
make forward FORWARD_PORT=8001   # terminal 2: deliver to the relay instead of the ingester
make sandbox-pr                  # terminal 3
```

This writes `opened.json`, `synchronize.json`, `edited.json`, `closed.json`, `reopened.json` and
`merged.json`, each shaped `{"headers": {"X-GitHub-Event": ..., "X-GitHub-Delivery": ...}, "body": {...}}`.
Existing files are never overwritten (delete one to re-record it). The signature header is not kept;
tests re-sign bodies with the test secret. Payloads contain public repo and user data only, but check
them before committing.

### Fallback: copy from Recent Deliveries

If `gh webhook forward` isn't an option, add a normal webhook in **Settings → Webhooks → Add webhook**:
payload URL (any URL; deliveries are recorded even if they fail), content type `application/json`,
your secret, "Let me select individual events" → **Pull requests** only. Run `make sandbox-pr`, then open
**Recent Deliveries**. For each delivery, copy `X-GitHub-Event` and `X-GitHub-Delivery` from the request
headers and the payload into a fixture file with the shape above, named after the action (`merged.json` for
the `closed` delivery whose `pull_request.merged` is `true`). Delete the webhook afterwards.
