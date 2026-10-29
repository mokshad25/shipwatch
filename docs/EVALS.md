# Evals

How well does the PR risk agent spot risky changes? This file holds the dataset, the method
used to label it and (from W5·D2 on) the numbers.

## Dataset (W5·D1)

`evals/data/prs.jsonl`: 40 merged PRs, 20 labelled `risky=1` and 20 labelled `risky=0`, built by
`evals/build_dataset.py` on 2026-10-26. One JSON object per line:

```json
{"repo": "astral-sh/ruff", "number": 18010, "label": 1,
 "reason": "reverted by #18150 1.0 days after merge", "title": "...", "author": "...",
 "merged_at": "2025-05-16T...Z", "evidence": {"kind": "revert", "by_pr": 18150, "days_after": 1.0}}
```

The file is frozen and committed. Rebuild it only when the dataset should change, since GitHub
keeps changing:

```
make dataset                                      # default repos, --since 2025-01-01, today
make dataset DATASET_ARGS="--as-of 2026-10-26"    # same cut-off as the committed file
```

### Sources

| Repo | Merged PRs scanned | Reverted ≤30 d | Hotfixed ≤7 d | risky=1 used | risky=0 used |
|---|---|---|---|---|---|
| astral-sh/ruff | 8,666 | 13 | 96 | 7 (7 revert) | 7 |
| astral-sh/uv | 6,029 | 32 | 45 | 6 (6 revert) | 6 |
| pydantic/pydantic | 965 | 7 | 14 | 7 (6 revert, 1 hotfix) | 7 |

Window: PRs merged from 2025-01-01 up to 30 days before the as-of date (2026-09-26), so every PR
has a complete 30-day follow-up window. Pure-Python repos were checked and dropped because they
have too few reverts (since 2024: black 5, fastapi 3, poetry 1, flask 0).

### Labeling rules

All rules work from one listing of every merged PR in the repo, plus timeline reads for sampled
negatives.

**risky=1, revert.** A merged PR whose title starts with `Revert ` reverts PR T when the body
says `Reverts owner/repo#T` (GitHub's revert button), or its title is `Revert "<T's title>"`,
optionally ending in `(#T)`. This is the same matching the agent's `author_history` tool uses
(`agent.tools.revert_target`). The revert must merge within 30 days of T. Re-lands (`Revert
"Revert ...`) are skipped, and so are targets that are reverts themselves.

**risky=1, hotfix.** A merged PR whose title contains `fix`, `fixes`, `fixed`, `hotfix` or
`regression`, that references T (`#T` or `.../pull/T`) in its title or body, and that merged
0–7 days after T. A reference is required, because about half of all PRs say "fix".

**risky=0, clean.** A merged PR that is not a revert, not written by a bot, not labelled risky,
and not the revert or fix behind a label. It must also have no trouble signal within 30 days:
- no PR in the listing whose title sounds like trouble (revert/fix/regression/bug/broke/crash)
  references it, and
- its timeline has no cross-reference from any issue or PR with such a title or a `bug`-like
  label.

**Selection.** Reverts are taken before hotfixes, since they are the stronger signal. Positives
are picked round-robin across repos, at most 10 per repo, with seeded shuffles (`--seed 0`).
Each positive gets one negative from the same repo, merged in the nearest month, so both classes
cover the same time span. Bot-authored PRs are excluded from both classes.

### Label spot-check

Six positives were checked by hand against the revert/fix PR's description:

| PR | Label evidence | Verdict |
|---|---|---|
| ruff#18010 | #18150: "introduced a serious performance regression" | correct |
| ruff#23544 | #23935: new dependency relies on unsound code | correct |
| uv#16744 | #16802: "I'll un-revert and fix tomorrow" | correct |
| pydantic#12865 | #12889: reverted because it touched V1 code (policy, not a bug) | noise |
| pydantic#12459 | #12462: a CI-job fix that didn't work (CI only, no product risk) | weak |
| pydantic#12334 | "hotfix" #12352 is a docstring typo fix | noise |

That is 3 of 6 clearly right. Expect the positive labels to be roughly 50–70% precise. Looking
at the titles, several more positives are CI/release changes (ruff#20361 loongarch builds,
ruff#21735 PEP 740 attestations, pydantic#12699 docs CI, uv#17505 a Claude Code hook), and
uv#16744 and uv#16805 are the same feature landed twice. W5·D6 error analysis should start by
re-checking these labels rather than blaming the agent.

### Known biases

- **Reverts aren't always bugs.** Changes get reverted for policy, release timing, splitting a
  PR, or a CI experiment. These become false "risky" labels (see the spot-check).
- **Unreferenced hotfixes are missed.** A fix that never names the PR it fixes is invisible, so
  some "clean" PRs are really risky. Only 1 of 20 positives came from a hotfix, because
  reverts take priority under the cap.
- **Survivorship.** A bug found more than 30 days after merge counts as clean.
- **Repo and culture.** Two of three repos come from the same org (Astral, mostly Rust) and
  revert fast (median well under a day). Repos that fix forward are under-represented. The
  results say little about other stacks.
- **Small n.** With 40 PRs, one flipped prediction moves recall by 5 points, and F1's 95%
  interval is roughly ±15 points. Treat differences under ~10 points as noise.
- **Leakage (must fix in W5·D2).** PRs are scored after the fact, so `author_history` would find
  the very revert that produced the label. Its `reverted` list is a direct answer key. The eval
  runner must cut tool data at the PR's `merged_at` (or at least drop the PR itself and anything
  merged after it) before scores mean anything. `file_churn` already ends its window at the PR's
  `created_at`.
- **Merged PRs only.** In production the agent sees open PRs, where CI may still be failing and
  review is ongoing. Here CI has usually been fixed and the PR approved, which hides the
  `failing_ci` signal.
- **Selection is not production's base rate.** The 50/50 split makes precision look far better
  than it would on a real stream, where well under 5% of PRs get reverted.

## Eval harness (W5·D2)

```
make eval                                             # .env's model, PROMPT_VERSION (v1)
make eval EVAL_ARGS="--prompt v2 --model groq:openai/gpt-oss-20b"
uv run python evals/compare.py                        # D3 table, from the cache
uv run python evals/tune_weights.py --apply           # D4, from the cache
```

`evals/run.py` runs the agent and the deterministic scorer on every PR in the dataset
(`--concurrency 4` by default, 1 on Groq's free tier). It writes
`evals/reports/<time>-<prompt>-<model>.json` and `.md`. Two caches make reruns cheap and
repeatable, and both are committed:

- **GitHub responses** (`evals/cache/github/`): every tool request is recorded once and then
  replayed. The agent sees the same diff, churn, history and checks on every run, and CI calls
  nothing but the LLM. `--offline` refuses any request that isn't in the cache. Reports say how
  many responses were fetched live.
- **Agent results** (`evals/cache/agent/<model>/<prompt>/<repo>-<pr>.json`): keyed by
  (PR, prompt version, model), with the verdict, the full trace and the raw diff size (so D4 can
  re-score without the LLM). Transient failures aren't cached: rate limits, provider outages,
  timeouts and GitHub errors stay *pending*. After 3 of them in a row the run stops starting new
  PRs, so on a free tier you just rerun the next day.

Rows run alternating risky and clean, rotating through the repos, so a run cut short by a token
cap is still balanced.

**Metrics.** A PR counts as predicted risky at a threshold when its level is that or higher.
"high" is the threshold the plan names; "medium" is reported too, because with the current
weights very few PRs reach high (55+). For each threshold the report gives precision, recall,
F1 and the confusion matrix, computed over *scored* PRs. `needs_review` verdicts are left out and
counted separately: the agent declined to judge. Also reported: average tokens per PR, cost per
PR (Groq on-demand prices in `evalkit.PRICES`) and p50/p95 latency. On a free tier, latency
includes the SDK waiting out per-minute token limits, so treat it as an upper bound.

**Leakage fix.** The eval runs the agent with `point_in_time=True`. `author_history` then only
searches PRs merged before the reviewed PR was opened (`merged:<created_at`), so the revert that
produced a label can't show up as evidence.

### What the harness found before any tuning

The first runs exposed a robustness bug rather than a judgement problem. Groq checks
structured output and tool calls on its own server. When a generation fails that check, Groq
returns HTTP 400 (`json_validate_failed`, `tool_use_failed`, `output_parse_failed`) instead of
the text, so the agent's "retry once on invalid output" path never ran and the PR became
`needs_review`:

| Model | Runs | Rejected by Groq (400) |
|---|---|---|
| gpt-oss-120b | 14 | 3 (21%) |
| gpt-oss-20b | 14 | 8 (57%) |

Fix (`agent/loop.py`): treat those 400s like any invalid output. The tool turn is retried once,
and the assessment gets its one corrective retry with the provider's error text. The affected
cached results were discarded and rerun with the fix, so every number below is post-fix.

A second free-tier artefact remains: Groq's free tier caps a *single request* at 8k tokens/min,
and a large PR's conversation can exceed that (HTTP 413). Those PRs are `needs_review` on the
free tier and would be scored on a paid tier.

## CI regression gate (W5·D5)

`.github/workflows/eval.yml` runs on PRs that touch `services/worker/src/agent/**` (the prompts,
`weights.yaml` and the agent), the eval code or its data:

- Runs the agent live on the fixed 10-PR subset in `evals/data/ci_subset.txt` (5 risky and 5
  clean, across all three repos), using the model named in `evals/baseline.json` (now
  `groq:openai/gpt-oss-20b`, see below) and the worker's default `PROMPT_VERSION`.
- GitHub comes from the committed response cache, so only the LLM is live (key: the
  `GROQ_API_KEY` repo secret).
- Fails if F1 at the baseline's threshold drops more than 5 points below `evals/baseline.json`,
  or if any PR didn't run.
- Posts the metrics table as a PR comment (updated on each push) and uploads the report.

Rebuild the baseline after an intended change has landed:

```
uv run python evals/run.py --subset evals/data/ci_subset.txt --threshold medium \
    --model groq:openai/gpt-oss-20b --write-baseline evals/baseline.json
```

**Why the gate runs on gpt-oss-20b.** The first baseline used gpt-oss-120b. On Groq's free tier
each model has its own daily cap of about 200k tokens, and one gate run uses about 150k, so two
gate runs in a day can't both finish. The demo PR (#2) used the 120b cap, and the W5 PR's own
gate (#3) then got HTTP 429 on every PR and failed without scoring any. On the CI subset,
gpt-oss-20b scores the same as 120b did (F1 0.727 at ≥ medium: 4 TP, 2 FP, 1 FN, 2 TN, and
ruff#18206 `needs_review` on both) at half the price per token, and its cap is separate from the
120b eval runs. The 120b baseline is in git history (`e857b00`).

Caveats:
- 10 PRs is coarse. One flipped prediction moves F1 by roughly 10 points, and the LLM isn't
  deterministic, so an unchanged prompt can occasionally fail the gate. Re-run the job before
  digging in.
- On Groq's free tier one gate run spends about 150k of the model's ~200k daily tokens and takes
  about 25 minutes, so only one gate run per day fits. A run that hits the cap fails with
  "N PRs didn't run (rate limits?)"; re-run it once the 24-hour window has moved on.
- gpt-oss-20b has Groq reject more of its generations than 120b (see above). The agent retries
  those once, but a run can still end with an extra `needs_review` and a slightly different F1.
