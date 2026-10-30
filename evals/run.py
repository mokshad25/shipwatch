"""Run the PR risk agent and scorer over the eval dataset and report how well it did.

    uv run python evals/run.py                                   # .env's model and prompt
    uv run python evals/run.py --prompt v2 --model groq:openai/gpt-oss-20b
    uv run python evals/run.py --subset evals/data/ci_subset.txt --offline --no-cache \\
        --baseline evals/baseline.json                           # the CI gate (W5 D5)

Writes evals/reports/<time>-<prompt>-<model>.json and .md and prints the markdown. Results are
cached per (PR, prompt, model) in evals/cache, so a rerun only runs what's missing; on a free
tier, rerun after the daily token cap resets until nothing is pending. Costs real money.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

import evalkit
from agent.llm import make_chat_model
from agent.prompts import load_prompt
from shipwatch_common.logging import configure_logging
from worker.settings import WorkerSettings


def _count(directory: Path) -> int:
    return sum(1 for _ in directory.glob("*.json")) if directory.is_dir() else 0


async def _main(args: argparse.Namespace) -> int:
    base = WorkerSettings()
    model = args.model or f"{base.llm_provider}:{base.llm_model}"
    args.prompt = args.prompt or base.prompt_version  # CI evaluates what the worker would run
    load_prompt(args.prompt)  # fail fast on an unknown version
    settings = evalkit.eval_settings(base, model, args.prompt)
    rows = evalkit.load_dataset(args.dataset, args.subset)
    if args.limit:
        rows = rows[: args.limit]
    llm = make_chat_model(settings)
    recorded_before = _count(args.cache / "github")
    async with evalkit.make_github(settings, args.cache, offline=args.offline) as gh:
        result = await evalkit.run_eval(
            rows,
            llm,
            gh,
            settings,
            # --resume keeps this run's results apart from evals/cache, so a rerun of the same
            # code only spends tokens on what's still pending.
            cache_root=args.resume or args.cache,
            use_cache=args.resume is not None or not args.no_cache,
            concurrency=args.concurrency,
        )
    if args.resume:
        evalkit.finish_resume(args.resume, result.pending)
    # Responses the GitHub cache didn't have yet: 0 means only the LLM was live.
    recorded = _count(args.cache / "github") - recorded_before
    dataset = str(args.subset or args.dataset)
    threshold = args.threshold
    baseline = json.loads(args.baseline.read_text()) if args.baseline else None
    if baseline:
        threshold = baseline["threshold"]
    report = evalkit.build_report(
        result,
        model=model,
        prompt=args.prompt,
        dataset=Path(dataset).name,
        n_rows=len(rows),
        threshold=threshold,
    )
    report["github_requests_recorded"] = recorded
    ok, gate = True, None
    if baseline:
        ok, gate = evalkit.check_gate(report, baseline, args.max_f1_drop)
        report["gate"] = {"ok": ok, "message": gate, "baseline": baseline}
    markdown = evalkit.render_markdown(report, gate)
    path = evalkit.write_report(report, markdown, args.out)
    if args.markdown_out:
        args.markdown_out.write_text(markdown)
    if args.write_baseline:
        f1 = report["metrics"]["thresholds"][threshold]["f1"]
        args.write_baseline.write_text(
            json.dumps(
                {
                    "threshold": threshold,
                    "f1": f1,
                    "model": model,
                    "prompt_version": args.prompt,
                    "dataset": report["dataset"],
                    "created_at": report["created_at"],
                },
                indent=2,
            )
            + "\n"
        )
    print(markdown)
    print(f"report: {path}")
    return 0 if ok else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--model", help="provider:model (default: LLM_PROVIDER:LLM_MODEL)")
    parser.add_argument(
        "--prompt", help="prompt version, agent/prompts/<v>.md (default: PROMPT_VERSION)"
    )
    parser.add_argument("--dataset", type=Path, default=evalkit.DATASET)
    parser.add_argument("--subset", type=Path, help="file of owner/repo#123 lines to run")
    parser.add_argument("--limit", type=int, help="only the first N rows")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--threshold", choices=evalkit.THRESHOLDS, default="high")
    parser.add_argument(
        "--offline", action="store_true", help="GitHub from the recorded cache only; no network"
    )
    parser.add_argument("--no-cache", action="store_true", help="ignore cached agent results")
    parser.add_argument("--cache", type=Path, default=evalkit.CACHE)
    parser.add_argument(
        "--resume",
        type=Path,
        help="read and write agent results here instead of the cache (the CI gate's reruns)",
    )
    parser.add_argument("--out", type=Path, default=evalkit.REPORTS)
    parser.add_argument("--baseline", type=Path, help="gate against this baseline.json")
    parser.add_argument("--max-f1-drop", type=float, default=5.0, help="points (0–100)")
    parser.add_argument("--write-baseline", type=Path, help="save this run as the baseline")
    parser.add_argument("--markdown-out", type=Path, help="also write the markdown here")
    args = parser.parse_args()
    configure_logging("WARNING")
    sys.exit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
