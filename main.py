#!/usr/bin/env python3
"""
ReconLoop — AI Finance Controller
Razorpay AI Buildathon 2026 · Track 04

Closes the loop between an internal order ledger and a payment-gateway
settlement report: matches what it can, and is honest about what it can't.

Usage:
    python3 main.py                  generate data if missing, run, report
    python3 main.py --regenerate     force a fresh synthetic dataset
    python3 main.py --seed 7         use a different synthetic dataset
    python3 main.py --use-llm        narrate exceptions with Claude (needs ANTHROPIC_API_KEY)
    python3 main.py --no-validate    skip the ground-truth accuracy check

Needs nothing beyond the Python 3.9+ standard library.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.data_generator import generate, DEFAULT_SEED
from src.data_loader import load_orders, load_settlements
from src.matcher import ReconciliationAgent
from src.explainer import explain_all
from src.report import build_summary, write_json_report, write_exceptions_csv, write_html_report
from src.validator import validate

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "output"

ORDERS_CSV = DATA_DIR / "order_ledger.csv"
SETTLEMENTS_CSV = DATA_DIR / "settlement_report.csv"
GROUND_TRUTH_JSON = DATA_DIR / "ground_truth.json"


def banner(text: str) -> None:
    print("\n" + "=" * 70)
    print(text)
    print("=" * 70)


def main() -> None:
    parser = argparse.ArgumentParser(description="ReconLoop — AI Finance Controller reconciliation agent")
    parser.add_argument("--regenerate", action="store_true", help="regenerate synthetic data before running")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="synthetic data seed")
    parser.add_argument("--use-llm", action="store_true", help="narrate exceptions with Claude (needs ANTHROPIC_API_KEY)")
    parser.add_argument("--no-validate", action="store_true", help="skip ground-truth accuracy validation")
    args = parser.parse_args()

    DATA_DIR.mkdir(exist_ok=True)
    OUTPUT_DIR.mkdir(exist_ok=True)

    if args.regenerate or not ORDERS_CSV.exists() or not SETTLEMENTS_CSV.exists():
        banner("GENERATING SYNTHETIC DATA")
        n_orders, n_settlements = generate(DATA_DIR, seed=args.seed)
        print(f"Wrote {n_orders} ledger orders and {n_settlements} settlement records to {DATA_DIR}/")

    banner("LOADING DATA")
    orders = load_orders(ORDERS_CSV)
    settlements = load_settlements(SETTLEMENTS_CSV)
    print(f"{len(orders)} ledger orders, {len(settlements)} settlement records.")

    banner("RUNNING RECONCILIATION AGENT")
    start = time.perf_counter()
    agent = ReconciliationAgent(orders, settlements)
    result = agent.run()
    elapsed = time.perf_counter() - start
    for line in result.trace:
        print("  " + line)
    print(f"\nProcessed {len(orders) + len(settlements)} records in {elapsed:.4f}s.")

    if args.use_llm:
        banner("NARRATING EXCEPTIONS WITH CLAUDE (optional)")
        touched = [r for r in (orders + settlements) if r.status is not None]
        explain_all(touched, use_llm=True)
        print("Done. (Silently falls back to the rule-based note if ANTHROPIC_API_KEY isn't set.)")

    validation = None
    if not args.no_validate:
        validation = validate(orders, settlements, GROUND_TRUTH_JSON)
        if validation:
            banner("ACCURACY VALIDATION (against synthetic ground truth)")
            print(
                f"  {validation['correctly_classified']}/{validation['ground_truth_records']} records "
                f"classified correctly ({validation['accuracy_percent']}%)"
            )
            if validation["misclassified"]:
                print(f"  Misclassified: {len(validation['misclassified'])} — see output/reconciliation_report.json for detail")

    summary = build_summary(orders, settlements, elapsed, validation)

    write_json_report(OUTPUT_DIR / "reconciliation_report.json", orders, settlements, result.trace, summary)
    write_exceptions_csv(OUTPUT_DIR / "exceptions.csv", orders, settlements)
    write_html_report(OUTPUT_DIR / "report.html", orders, settlements, result.trace, summary)

    banner("SUMMARY")
    print(f"  Order match rate:       {summary['order_match_rate_percent']}%")
    print(f"  Settlement match rate:  {summary['settlement_match_rate_percent']}%")
    print(f"  Exceptions:             {summary['exceptions_count']}  {summary['exception_breakdown']}")
    print(f"  Flags:                  {summary['flags_count']}  {summary['flag_breakdown']}")
    rps = summary["throughput"]["records_per_second"]
    print(f"  Throughput:             {rps} records/sec" if rps is not None else "  Throughput:             n/a")
    print(f"\nReports written to {OUTPUT_DIR}/")
    print("  - report.html                  (open this one first)")
    print("  - reconciliation_report.json")
    print("  - exceptions.csv")


if __name__ == "__main__":
    main()
