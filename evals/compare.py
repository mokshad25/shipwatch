"""Compare prompt versions × models from cached eval results (W5 D3); no LLM calls.

    uv run python evals/compare.py --prompts v1 v2 \\
        --models groq:openai/gpt-oss-120b groq:openai/gpt-oss-20b

Run evals/run.py for each pair first. Rows are compared on the PRs every pair has a result for,
so a pair that is still pending (free-tier caps) doesn't skew the table; coverage is shown.
"""

import argparse
from pathlib import Path
from typing import Any

import evalkit


def compare(
    rows: list[dict[str, Any]], prompts: list[str], models: list[str], cache_root: Path
) -> tuple[str, list[dict[str, Any]]]:
    """Markdown table plus one dict per (prompt, model) with its metrics on the common PRs."""
    cached = {(p, m): evalkit.load_cached(rows, m, p, cache_root) for p in prompts for m in models}
    common = set.intersection(*({r.key for r in recs} for recs in cached.values()))
    lines = [
        f"Compared on the {len(common)} PRs (of {len(rows)}) every pair has a result for.",
        "",
        "| Prompt | Model | Done | F1 ≥high | Recall ≥high | F1 ≥medium | Precision ≥medium "
        "| Recall ≥medium | Tokens/PR | Cost/PR | Latency p50 / p95 |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    out = []
    for (prompt, model), records in cached.items():
        m = evalkit.metrics([r for r in records if r.key in common])
        high, medium = m["thresholds"]["high"], m["thresholds"]["medium"]
        lines.append(
            f"| {prompt} | `{model.split(':', 1)[1]}` | {len(records)}/{len(rows)} "
            f"| {high['f1']:.2f} | {high['recall']:.2f} | {medium['f1']:.2f} "
            f"| {medium['precision']:.2f} | {medium['recall']:.2f} | {m['avg_tokens']} "
            f"| ${m['cost_per_pr']:.4f} | {_s(m['latency_p50_s'])} / {_s(m['latency_p95_s'])} |"
        )
        out.append({"prompt": prompt, "model": model, "done": len(records), "metrics": m})
    return "\n".join(lines) + "\n", out


def _s(value: float | None) -> str:
    return "-" if value is None else f"{value:.0f} s"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--prompts", nargs="+", default=["v1", "v2"])
    parser.add_argument(
        "--models", nargs="+", default=["groq:openai/gpt-oss-120b", "groq:openai/gpt-oss-20b"]
    )
    parser.add_argument("--dataset", type=Path, default=evalkit.DATASET)
    parser.add_argument("--cache", type=Path, default=evalkit.CACHE)
    args = parser.parse_args()
    table, _ = compare(evalkit.load_dataset(args.dataset), args.prompts, args.models, args.cache)
    print(table)


if __name__ == "__main__":
    main()
