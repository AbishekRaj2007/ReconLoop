"""Score the reconciliation pipeline against ground truth (plan.md §9
Phase 5 deliverable): a single command that produces precision/recall/F1,
false-match rate, missed-match rate, per-tier attribution, and throughput —
reproducibly, from a fresh clone.

Tier 3/4 only run with --use-llm (see run_pipeline.py's own docstring for
why); without it, this scores whatever Tier 1 + Tier 2 alone resolved,
which is an honest (if lower) number, not a guess at what Tier 3/4 would
have done.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_pipeline

from app.core.scoring.evaluator import score_pipeline


def load_ground_truth(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text())


def run_scoring(
    raw_dir: Path,
    config_path: Path,
    ground_truth_path: Path,
    use_llm: bool = False,
) -> dict[str, Any]:
    ground_truth = load_ground_truth(ground_truth_path)
    pipeline_report = run_pipeline.run_pipeline(raw_dir, config_path, use_llm=use_llm)
    scoring = score_pipeline(
        ground_truth, pipeline_report["matches"], pipeline_report["total"]
    )

    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "total_records": pipeline_report["total"],
        "tier1_matched": pipeline_report["tier1_matched"],
        "matched": len(pipeline_report["matches"]),
        "pending_llm_count": pipeline_report["pending_llm_count"],
        "exception_count": len(pipeline_report["exceptions"]),
        "exception_type_counts": dict(pipeline_report["exception_type_counts"]),
        "precision": scoring.precision,
        "recall": scoring.recall,
        "f1": scoring.f1,
        "false_match_rate": scoring.false_match_rate,
        "missed_match_rate": scoring.missed_match_rate,
        "per_source_pair": {
            pair: {
                "precision": counts.precision,
                "recall": counts.recall,
                "f1": counts.f1,
                "true_positives": counts.true_positives,
                "false_positives": counts.false_positives,
                "false_negatives": counts.false_negatives,
                "true_negatives": counts.true_negatives,
            }
            for pair, counts in scoring.per_source_pair.items()
        },
        "tier_attribution": scoring.tier_attribution,
        "wall_clock_seconds": pipeline_report["wall_clock_seconds"],
        "records_per_second": pipeline_report["records_per_second"],
        "used_llm": use_llm,
        "total_llm_calls": pipeline_report.get("total_llm_calls", 0),
        "total_llm_cost_usd": float(
            pipeline_report.get("total_llm_cost_usd", Decimal(0))
        ),
    }


def print_report(report: dict[str, Any]) -> None:
    print(f"ReconLoop Scoring Report — {report['timestamp']}")
    print(f"Total records:                 {report['total_records']}")
    print(f"Matched:                       {report['matched']}")
    print(f"Pending Tier 3/4:              {report['pending_llm_count']}")
    print(f"Exceptions:                    {report['exception_count']}")
    print()
    print("--- Precision / Recall / F1 ---")
    print(
        f"Overall:       precision={report['precision']:.3f}  "
        f"recall={report['recall']:.3f}  f1={report['f1']:.3f}"
    )
    for pair, stats in report["per_source_pair"].items():
        print(
            f"{pair:15s}precision={stats['precision']:.3f}  "
            f"recall={stats['recall']:.3f}  f1={stats['f1']:.3f}"
        )
    print()
    print("--- Honesty metrics (reported even if zero) ---")
    print(f"False-match rate:              {report['false_match_rate']:.1%}")
    print(f"Missed-match rate:             {report['missed_match_rate']:.1%}")
    print()
    print("--- Per-tier match attribution (% of total records) ---")
    for tier in ("TIER_1", "TIER_2", "TIER_3", "TIER_4"):
        pct = report["tier_attribution"].get(tier, 0.0)
        print(f"  {tier:<8} {pct:.1%}")
    print()
    print("--- Throughput ---")
    print(f"Wall-clock time:               {report['wall_clock_seconds']:.3f}s")
    print(f"Records/sec:                   {report['records_per_second']:.1f}")
    print(f"LLM calls made:                {report['total_llm_calls']}")
    print(f"Estimated LLM cost (USD):      ${report['total_llm_cost_usd']:.4f}")


def append_metrics_log(report: dict[str, Any], path: Path) -> None:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "# ReconLoop Metrics Log\n\n"
            "Append-only history of `run_scoring.py` runs (plan.md §9 Phase 5).\n\n"
        )

    entry = (
        f"## {report['timestamp']}\n\n"
        f"- Total records: {report['total_records']} "
        f"(matched: {report['matched']}, Tier 1: {report['tier1_matched']}, "
        f"pending Tier 3/4: {report['pending_llm_count']}, "
        f"exceptions: {report['exception_count']})\n"
        f"- Precision / Recall / F1: {report['precision']:.3f} / "
        f"{report['recall']:.3f} / {report['f1']:.3f}\n"
        f"- False-match rate: {report['false_match_rate']:.1%}\n"
        f"- Missed-match rate: {report['missed_match_rate']:.1%}\n"
        f"- Wall-clock: {report['wall_clock_seconds']:.3f}s "
        f"({report['records_per_second']:.1f} records/sec)\n"
        f"- LLM calls: {report['total_llm_calls']}, "
        f"estimated cost: ${report['total_llm_cost_usd']:.4f}\n\n"
    )
    with path.open("a") as f:
        f.write(entry)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=REPO_ROOT / "data" / "raw")
    parser.add_argument(
        "--ground-truth",
        type=Path,
        default=REPO_ROOT / "data" / "ground_truth" / "ground_truth_matches.json",
    )
    parser.add_argument(
        "--config", type=Path, default=REPO_ROOT / "config" / "matching_config.yaml"
    )
    parser.add_argument(
        "--use-llm",
        action="store_true",
        help="Run Tier 3/4 on escalated records. Requires GROQ_API_KEY and "
        "ANTHROPIC_API_KEY (see .env.example) and makes real, billed API calls.",
    )
    parser.add_argument(
        "--json-out", type=Path, default=None, help="Write the full report as JSON."
    )
    parser.add_argument(
        "--metrics-log", type=Path, default=REPO_ROOT / "docs" / "metrics_log.md"
    )
    parser.add_argument(
        "--no-log",
        action="store_true",
        help="Don't append to the metrics log (useful for test/fixture runs).",
    )
    args = parser.parse_args()

    if args.use_llm:
        from dotenv import load_dotenv

        load_dotenv(REPO_ROOT / ".env")

    try:
        report = run_scoring(
            args.raw_dir, args.config, args.ground_truth, use_llm=args.use_llm
        )
    except Exception as exc:
        if args.use_llm:
            raise SystemExit(
                "--use-llm requires GROQ_API_KEY and ANTHROPIC_API_KEY to be set "
                f"(see .env.example). Underlying error: {exc}"
            ) from exc
        raise

    print_report(report)

    if args.json_out:
        args.json_out.write_text(json.dumps(report, indent=2))
        print(f"\nJSON report written to {args.json_out}")

    if not args.no_log:
        append_metrics_log(report, args.metrics_log)
        print(f"Appended to {args.metrics_log}")


if __name__ == "__main__":
    main()
