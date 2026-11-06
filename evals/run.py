"""Eval filtera: preciznost/odziv digesta, tačnost tipa i roka (posle reranka).

    python -m evals.run [--dataset evals/dataset.jsonl] [--profile evals/profile.json]
                        [--min-precision 0.7] [--baseline evals/baseline.json] [--save-baseline]

Primer u datasetu (jedan JSON po redu):
    {"title": "...", "org": "...", "location": "Beograd", "text": "...",
     "relevant": true, "type": "praksa", "deadline": "2026-11-20"}
`type` i `deadline` su opcioni; ako ih nema, ne ulaze u odgovarajuću metriku.
Pad preciznosti za više od 5 p.p. u odnosu na baseline obara build (exit 1).
"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from radar.config import get_settings
from radar.llm import LLM, AnthropicLLM, LLMInvalidOutput
from radar.models import Opportunity
from radar.profile import Profile
from radar.rank import hard_reject
from radar.rerank import RerankOut, build_system, build_user

MAX_DROP = 0.05


def evaluate(
    examples: list[dict], profile: Profile, llm: LLM, today: date, model: str | None = None
) -> dict:
    filt = profile.to_filter()
    system = build_system(profile, "")
    tp = fp = fn = tn = 0
    type_total = type_ok = deadline_total = deadline_ok = invalid = 0

    for ex in examples:
        opp = Opportunity(
            url=ex.get("url", "https://eval.invalid/" + ex["title"]),
            title=ex["title"],
            org=ex.get("org"),
            location=ex.get("location"),
            deadline=None,  # rok izvlači model; poredi se sa očekivanim
            raw_text=ex.get("text", ""),
            source="eval",
            type=ex.get("guessed_type", "posao"),
        )
        predicted = False
        out = None
        if hard_reject(opp, filt, today, strict_type=False) is None:
            try:
                out = llm.complete_json(
                    system=system, user=build_user(opp), schema=RerankOut, model=model
                )
            except LLMInvalidOutput:
                invalid += 1
            else:
                predicted = (
                    out.relevance >= profile.prag_relevantnosti and out.type in profile.tipovi
                )
        actual = bool(ex["relevant"])
        tp += predicted and actual
        fp += predicted and not actual
        fn += (not predicted) and actual
        tn += (not predicted) and not actual

        if out is not None and "type" in ex:
            type_total += 1
            type_ok += out.type == ex["type"]
        if out is not None and "deadline" in ex:
            deadline_total += 1
            expected = ex["deadline"]
            deadline_ok += (out.deadline.isoformat() if out.deadline else None) == expected

    def ratio(a: int, b: int) -> float | None:
        return round(a / b, 4) if b else None

    return {
        "n": len(examples),
        "precision": ratio(tp, tp + fp),
        "recall": ratio(tp, tp + fn),
        "type_accuracy": ratio(type_ok, type_total),
        "deadline_accuracy": ratio(deadline_ok, deadline_total),
        "invalid": invalid,
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
    }  # fmt: skip


def check(metrics: dict, min_precision: float | None, baseline: dict | None) -> list[str]:
    failures = []
    p = metrics["precision"]
    if p is None:
        failures.append("nijedna stavka nije prošla prag: preciznost nije definisana")
        return failures
    if min_precision is not None and p < min_precision:
        failures.append(f"preciznost {p:.2f} < minimum {min_precision:.2f}")
    if baseline and baseline.get("precision") is not None and p < baseline["precision"] - MAX_DROP:
        failures.append(
            f"preciznost {p:.2f} je pala za više od 5 p.p. (baseline {baseline['precision']:.2f})"
        )
    return failures


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="evals/dataset.jsonl")
    ap.add_argument("--profile", default="evals/profile.json")
    ap.add_argument("--min-precision", type=float, default=None)
    ap.add_argument("--baseline", default="evals/baseline.json")
    ap.add_argument("--save-baseline", action="store_true")
    ap.add_argument("--today", default=None, help="YYYY-MM-DD (podrazumevano danas)")
    args = ap.parse_args(argv)

    settings = get_settings()
    if not settings.anthropic_api_key:
        print("ANTHROPIC_API_KEY nije postavljen")
        return 1
    lines = Path(args.dataset).read_text(encoding="utf-8").splitlines()
    examples = [json.loads(ln) for ln in lines if ln.strip()]
    profile = Profile(**json.loads(Path(args.profile).read_text(encoding="utf-8")))
    today = date.fromisoformat(args.today) if args.today else date.today()

    metrics = evaluate(
        examples, profile, AnthropicLLM(settings.anthropic_api_key, settings.rerank_model), today
    )
    print(json.dumps(metrics, indent=2, ensure_ascii=False))

    baseline_path = Path(args.baseline)
    baseline = json.loads(baseline_path.read_text()) if baseline_path.exists() else None
    failures = check(metrics, args.min_precision, baseline)
    if args.save_baseline:
        baseline_path.write_text(json.dumps(metrics, indent=2, ensure_ascii=False) + "\n")
        print(f"baseline sačuvan: {baseline_path}")
    for f in failures:
        print("FAIL:", f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
