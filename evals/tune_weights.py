"""Tune weights.yaml on cached eval results with a 70/30 train/test split (W5 D4); no LLM calls.

    uv run python evals/tune_weights.py --model groq:openai/gpt-oss-120b --prompt v1
    uv run python evals/tune_weights.py ... --apply      # write weights.yaml if test F1 improves

The LLM's factors are fixed (cached); only the deterministic score changes, so this is a cheap
coordinate search: one factor weight or level threshold at a time, keeping a change only if it
raises train F1. Train and test F1 are both reported so overfitting is visible.
"""

import argparse
import random
import re
from pathlib import Path
from typing import Any

import evalkit
from agent.scoring import WEIGHTS_PATH, Weights, load_weights, score

WEIGHT_STEPS = list(range(0, 21, 2))
LEVEL_STEPS = list(range(10, 81, 5))


def split(
    records: list[evalkit.EvalRecord], seed: int = 0, train_share: float = 0.7
) -> tuple[list[evalkit.EvalRecord], list[evalkit.EvalRecord]]:
    """Stratified by label, so both halves keep the class balance."""
    rng = random.Random(seed)
    train, test = [], []
    for label in (1, 0):
        group = sorted((r for r in records if r.label == label), key=lambda r: r.key)
        rng.shuffle(group)
        cut = round(len(group) * train_share)
        train += group[:cut]
        test += group[cut:]
    return train, test


def f1(records: list[evalkit.EvalRecord], weights: Weights, threshold: str) -> float:
    pairs = []
    for r in records:
        level = score(r.verdict.assessment, r.stats, weights).level
        pairs.append((r.label, evalkit.predicted(level, threshold)))
    return evalkit.classification(pairs)["f1"]


def _with(weights: Weights, field: str, value: int) -> Weights:
    data = weights.model_dump()
    if field in ("medium", "high"):
        data["levels"][field] = value
        if data["levels"]["medium"] >= data["levels"]["high"]:
            return weights  # keep the levels ordered
    else:
        data["factor_weights"][field] = value
    return Weights.model_validate(data)


def coordinate_search(
    train: list[evalkit.EvalRecord], start: Weights, threshold: str, passes: int = 4
) -> Weights:
    best, best_f1 = start, f1(train, start, threshold)
    fields = [*start.factor_weights, "medium", "high"]
    for _ in range(passes):
        improved = False
        for field in fields:
            steps = LEVEL_STEPS if field in ("medium", "high") else WEIGHT_STEPS
            for value in steps:
                candidate = _with(best, field, value)
                value_f1 = f1(train, candidate, threshold)
                if value_f1 > best_f1 + 1e-9:  # strictly better, so ties keep the current value
                    best, best_f1, improved = candidate, value_f1, True
        if not improved:
            break
    return best


def changes(old: Weights, new: Weights) -> dict[str, tuple[float, float]]:
    out = {
        k: (old.factor_weights[k], v)
        for k, v in new.factor_weights.items()
        if v != old.factor_weights[k]
    }
    for level in ("medium", "high"):
        a, b = getattr(old.levels, level), getattr(new.levels, level)
        if a != b:
            out[f"levels.{level}"] = (a, b)
    return out


def apply(path: Path, new: Weights) -> None:
    """Rewrite just the numbers in weights.yaml, keeping its comments and layout."""
    text = path.read_text()
    for kind, value in new.factor_weights.items():
        text = re.sub(rf"(?m)^(  {kind}: )\S+", rf"\g<1>{value:g}", text, count=1)
    for level in ("medium", "high"):
        value = getattr(new.levels, level)
        text = re.sub(rf"(?m)^(  {level}: )\S+", rf"\g<1>{value}", text, count=1)
    path.write_text(text)


def tune(records: list[evalkit.EvalRecord], threshold: str, seed: int = 0) -> dict[str, Any]:
    usable = [r for r in records if r.scored and r.stats is not None]
    train, test = split(usable, seed)
    old = load_weights()
    new = coordinate_search(train, old, threshold)
    result = {
        "threshold": threshold,
        "train": len(train),
        "test": len(test),
        "before": {"train_f1": f1(train, old, threshold), "test_f1": f1(test, old, threshold)},
        "after": {"train_f1": f1(train, new, threshold), "test_f1": f1(test, new, threshold)},
        "changes": changes(old, new),
        "weights": new,
    }
    result["keep"] = result["after"]["test_f1"] > result["before"]["test_f1"]
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--model", default="groq:openai/gpt-oss-120b")
    parser.add_argument("--prompt", default="v1")
    parser.add_argument("--threshold", choices=evalkit.THRESHOLDS, default="medium")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--dataset", type=Path, default=evalkit.DATASET)
    parser.add_argument("--cache", type=Path, default=evalkit.CACHE)
    parser.add_argument("--apply", action="store_true", help="write weights.yaml if test improves")
    args = parser.parse_args()
    records = evalkit.load_cached(
        evalkit.load_dataset(args.dataset), args.model, args.prompt, args.cache
    )
    r = tune(records, args.threshold, args.seed)
    print(f"{len(records)} cached results; train {r['train']}, test {r['test']} (seed {args.seed})")
    print(f"F1 at ≥ {r['threshold']}:")
    for name in ("before", "after"):
        print(f"  {name:6}  train {r[name]['train_f1']:.3f}  test {r[name]['test_f1']:.3f}")
    for field, (a, b) in r["changes"].items():
        print(f"  {field}: {a:g} -> {b:g}")
    if not r["changes"]:
        print("  no change improved train F1")
    if args.apply and r["keep"]:
        apply(WEIGHTS_PATH, r["weights"])
        print(f"test F1 improved: wrote {WEIGHTS_PATH}")
    elif args.apply:
        print("test F1 did not improve: weights.yaml left as is")


if __name__ == "__main__":
    main()
