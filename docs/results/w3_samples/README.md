# W3·D5: the agent on 10 real PRs

**Repo:** encode/httpx, the 10 most recently merged PRs. Seven target the `v1` branch, three
target `master`.
**Model:** Groq `openai/gpt-oss-120b`, at $0.15 / $0.60 per million tokens (free/on-demand tier).

The generated table is [summary.md](summary.md), with one JSON file per PR (PR, verdict, full
trace) here. Both are rebuilt with:

```bash
uv run python scripts/run_samples.py encode/httpx --limit 10
```

The three PRs that hit the daily cap can be re-run alone with
`uv run python scripts/run_samples.py encode/httpx --prs 3673 3672 3670`.

## Results (run 2, after fixes)

| PR | What it is | Verdict | Looks right? |
|---|---|---|---|
| #3773 | Test assertion tweak (1 file, +4/−1) | low 0 | ✅ |
| #3730 | Drop Python 3.10 from the CI matrix | low 6 (config_change 1) | ✅ |
| #3699 | Add `FunctionAuth` to `__all__` (+6/−1) | **medium 30** | ❌ inflated, see 1 |
| #3703 | Docs typo | low 0 | ✅ |
| #3692 | Docs example fix | low 0 | ✅ |
| #3690 | `.wait_ready` in server/parser loops, 4 src files, no tests | medium 32 | ✅ defensible (see 4) |
| #3677 | Dependabot: setup-python v5 → v6 | low 6 | ✅ |
| #3673 | Connection resets (8 files, `_pool` / `_network`) | needs_review | Groq daily token cap |
| #3672 | Server connection handling (8 files, `_pool` / `_network`) | needs_review | Groq daily token cap |
| #3670 | `FileStream` API change (breaking, public) | needs_review | Groq daily token cap |

**7 of 10 scored, for $0.013 total.** A small PR costs about $0.0014 and 6–7k tokens, and #3690
took 13k. Mean latency was 44 s, but that's mostly waiting out Groq's 8k tokens-per-minute limit:
#3773, the first run before any waiting, took 8 s. The last three PRs hit Groq's **200k
tokens-per-day** cap, because three runs on the same day had used it up. The agent returned
`needs_review` with the reason, as D6 intends.

For #3672 and #3670 there are also run-1 verdicts (before the fixes) and a single-PR re-run of
#3672 after the strict fix. They are used below as evidence and labelled as such.

## Which verdicts look wrong, and why

1. **#3699 is inflated: medium 30 for a two-line additive export.** The diff only adds
   `"FunctionAuth"` to two `__all__` lists. The class already exists in `httpx/_auth.py`.
   - The model said it adds "a new public symbol without accompanying implementation". That's
     false, and the tool output it had (the patch) shows the class is already there.
   - It also raised `missing_tests` at severity 2 for a re-export, and `risky_paths` at severity 2.
   - Run 1 scored the same PR **8 / low**. The swing comes entirely from which factors the LLM
     chose to list.
2. **#3672 under-scores connection-handling changes: low 0** (single-PR re-run after the strict
   fix).
   - It changes `_pool.py`, `_network.py`, `_server.py` and `_parsers.py` in both the sync and
     async packages. That is concurrency and connection-lifecycle code, exactly what
     `risky_paths` is meant for.
   - The model listed no factors, with confidence 0.96.
   - In run 1 the same PR failed to parse twice. That is fixed (see below).
3. **#3673 (run 1) had the wrong factor: low 12 from `large_diff` severity 2 for 89 lines.** 89
   lines isn't large: the size rule gives it 0 points. The real risk is the connection-reset
   handling in `_pool` / `_network`, and it was missed as in #3672. The summary even contradicts the
   factor ("modest size").
4. **#3690 is right for the right reasons, but only in run 2.** Core server and parser loops changed
   with no test files touched, so medium is reasonable. Run 1 gave it **0**: same diff, same tools,
   no factors.
5. **#3670 (run 1): low 16 for a breaking public API change.** The model correctly found
   `risky_paths` (2): the `FileStream` signature changed and callers must use
   `httpx.File(path).encode()`. But the weights can't reach medium on this alone: even severity 3
   is 8 × 3 = 24, one point under the medium threshold of 25.

## Bugs found and fixed during D5

| Bug | Effect in run 1 | Fix |
|---|---|---|
| `file_churn` counted commits up to *today*, on the repo's *default* branch | All 10 PRs showed 0 commits per file. The PRs are 8–13 months old, and seven target `v1`, whose `src/…` files don't exist on `master`. `hot_files` could never fire. | The window ends at the PR's `created_at` (`until=`), and commits are counted on its base branch (`sha=`). Run 2: #3690's files show 2 commits each. |
| gpt-oss on Groq echoed the schema's shape (`{"description": "", "properties": {…}}`) instead of the object | #3672 got needs_review (invalid twice), and #3673 needed the retry | `strict=True` on providers that support it (OpenAI, Groq). Run 2: 0 parse failures in 7 runs. |
| A 429 used up all worker attempts within seconds | One PR uses ~10k tokens against an 8k/min limit, so every attempt failed | The worker waits for the provider's `retry-after` (capped at 30 s). Batch scripts let the SDK retry (`LLM_MAX_RETRIES`). |
| Key in `.env` had a stray `=` and trailing space | 401 Invalid API Key | Fixed in `.env` (one line). |

## What this says about the design (input for week 5)

- **LLM factor choice is the main source of error, and it isn't stable.** Two runs of the same PR
  moved #3699 from 8 to 30 and #3690 from 0 to 32, crossing a level boundary both times. The
  deterministic score does its job (same factors, same score), but the factors themselves vary.
  Week 5 should:
  - **Compute the factors that can be computed.** `missing_tests` (src changed, no test files),
    `new_author` (merged_count), `hot_files` (churn threshold), `failing_ci` (state),
    `large_diff` (already size points), `migration` / `config_change` (path patterns). The LLM then
    only judges `risky_paths` and writes evidence and summary.
  - Measure stability: run each eval PR 3× and report how often the level agrees.
- **`risky_paths` is under-recognised for concurrency and connection code** (#3672, #3673). Name
  connection pools, sockets, retries and timeouts explicitly in the factor guide, or let a
  path-pattern list (`_pool`, `_network`, `_transports`) raise a candidate for the model to accept
  or reject.
- **Weights:** a single severity-3 `risky_paths` (24) can't reach medium (25). Either raise
  `risky_paths` to 9, or add a rule such as `risky_paths ≥ 3` → at least medium.
- **Evidence quality:** mostly verbatim tool output, as the prompt asks. One gpt-oss citation token
  leaked into evidence (`【get_diff†files.0.filename】`, #3730), and #3699's summary made a claim
  the tool output contradicts. A cheap check would be to require each factor's evidence to contain
  a file name or number that appears in the trace.
- **Free-tier Groq is enough for development, not for a pipeline.** At about 10k tokens per PR,
  200k tokens/day is roughly 20 PRs a day, and 8k tokens/min means one PR at a time with waits.
  When the cap is hit, the worker stores `needs_review` (verified above), which is the intended
  failure mode. For real use, a paid tier or a smaller model (e.g. `openai/gpt-oss-20b`, half the
  price, with its own limits) would be needed.
