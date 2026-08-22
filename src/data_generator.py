"""
data_generator.py — builds the synthetic batch ReconLoop reconciles.

Produces two CSVs that mimic a real Razorpay merchant's data:
  - order_ledger.csv       : the merchant's own order records
  - settlement_report.csv  : Razorpay's settlement report for those orders

...and one JSON file that the matching agent is NEVER shown:
  - ground_truth.json      : the status each record was deliberately built to be

matcher.py has zero knowledge of ground_truth.json. It exists purely so
validator.py can grade the agent's homework after the fact — that's what
makes the "measured accuracy" number in the final report real instead of
a claim.

Field names (order_id, payment_id, settlement_id, settlement_utr, fee, tax)
follow Razorpay's own settlement/order vocabulary so the demo reads like a
real merchant's reconciliation job.
"""

from __future__ import annotations

import csv
import json
import random
import string
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import List, Tuple

DEFAULT_SEED = 2026

MERCHANT_NAME = "Kestrel Living Pvt Ltd"  # fictional D2C home & lifestyle brand

CHANNELS = ["Website", "Mobile App", "Marketplace"]
METHODS_WEIGHTED = (
    ["upi"] * 55 + ["card"] * 25 + ["netbanking"] * 12 + ["wallet"] * 8
)  # rough real-world Indian D2C payment mix

FEE_RATE_BY_METHOD = {"upi": 0.005, "card": 0.020, "netbanking": 0.020, "wallet": 0.018}
GST_RATE = 0.18  # GST on the gateway fee

PRICE_POINTS = [
    299, 349, 399, 499, 599, 699, 799, 899, 999, 1199, 1299, 1499, 1799,
    1999, 2499, 2999, 3499, 3999, 4999, 5999, 7999, 9999, 12999, 14999,
]

FIRST_NAMES = [
    "Aarav", "Vivaan", "Aditya", "Vihaan", "Arjun", "Sai", "Reyansh", "Krishna",
    "Ishaan", "Rohan", "Ananya", "Diya", "Saanvi", "Aadhya", "Kiara", "Myra",
    "Priya", "Neha", "Pooja", "Sneha", "Karthik", "Vikram", "Rahul", "Amit",
    "Divya", "Meera", "Nisha", "Rajesh", "Suresh", "Anjali",
]
LAST_NAMES = [
    "Sharma", "Verma", "Gupta", "Nair", "Iyer", "Reddy", "Rao", "Menon",
    "Patel", "Shah", "Kapoor", "Malhotra", "Joshi", "Pillai", "Bhat", "Chatterjee",
]

BATCH_START = date(2026, 8, 1)
BATCH_END_ORDER = date(2026, 8, 18)  # last order date, leaves room for T+15 settlement


def _rand_token(rng: random.Random, length: int) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(rng.choice(alphabet) for _ in range(length))


def _make_order_id(rng: random.Random) -> str:
    return f"order_{_rand_token(rng, 14)}"


def _make_payment_id(rng: random.Random) -> str:
    return f"pay_{_rand_token(rng, 14)}"


def _make_settlement_id(rng: random.Random) -> str:
    return f"setl_{_rand_token(rng, 12)}"


def _make_utr(rng: random.Random) -> str:
    digits = "".join(rng.choice(string.digits) for _ in range(10))
    letters = "".join(rng.choice(string.ascii_lowercase) for _ in range(6))
    return digits + letters


def _random_name(rng: random.Random) -> str:
    return f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"


def _random_order_date(rng: random.Random) -> date:
    span = (BATCH_END_ORDER - BATCH_START).days
    return BATCH_START + timedelta(days=rng.randint(0, span))


def _fee_and_tax(amount: float, method: str) -> Tuple[float, float]:
    fee = round(amount * FEE_RATE_BY_METHOD[method], 2)
    tax = round(fee * GST_RATE, 2)
    return fee, tax


def _fuzzy_variant(order_id: str, rng: random.Random) -> str:
    """Small, realistic formatting drift — the kind a legacy ERP export
    introduces, not a random string. Deliberately gentle (single-character
    scale) so an exact match fails but a similarity-ratio fuzzy match
    (>= FUZZY_REF_THRESHOLD in matcher.py) reliably succeeds — a full-string
    case flip was tried and rejected here because it changes too much of
    the value (similarity ratio ~0.5, well under threshold)."""
    kind = rng.choice(["swap", "truncate", "charsub"])
    chars = list(order_id)
    if kind == "swap":
        i = len(order_id) // 2
        chars[i], chars[i + 1] = chars[i + 1], chars[i]
        return "".join(chars)
    if kind == "truncate":
        return order_id[:-1]  # simulates a fixed-width legacy export dropping the last char
    # charsub: one lookalike character swapped near the end (e.g. a legacy system's o/0 confusion)
    idx = -3
    chars[idx] = "0" if chars[idx] != "0" else "1"
    return "".join(chars)


@dataclass
class _Row:
    order: dict
    settlements: List[dict]
    order_truth: str
    settlement_truths: List[str]


def _build_order(rng: random.Random, order_id: str, amount: float) -> dict:
    return {
        "order_id": order_id,
        "order_date": _random_order_date(rng).isoformat(),
        "customer_name": _random_name(rng),
        "channel": rng.choice(CHANNELS),
        "order_amount": amount,
        "order_status": "Completed",
    }


def _build_settlement(rng: random.Random, order_id: str, amount: float, order_date: date, delay_days: int) -> dict:
    method = rng.choice(METHODS_WEIGHTED)
    fee, tax = _fee_and_tax(amount, method)
    return {
        "settlement_id": _make_settlement_id(rng),
        "payment_id": _make_payment_id(rng),
        "order_id": order_id,
        "method": method,
        "settled_at": (order_date + timedelta(days=delay_days)).isoformat(),
        "gross_amount": amount,
        "fee": fee,
        "tax": tax,
        "settlement_utr": _make_utr(rng),
    }


def generate(out_dir: Path, seed: int = DEFAULT_SEED,
             n_clean: int = 34, n_delayed: int = 6, n_amount_mismatch: int = 5,
             n_fuzzy_ref: int = 4, n_unsettled: int = 5, n_unidentified: int = 4,
             n_duplicate: int = 3) -> Tuple[int, int]:
    """Writes order_ledger.csv, settlement_report.csv, and ground_truth.json
    to out_dir. Returns (n_orders, n_settlements)."""

    rng = random.Random(seed)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    orders: List[dict] = []
    settlements: List[dict] = []
    gt_orders: dict = {}
    gt_settlements: dict = {}
    used_order_ids = set()

    def fresh_order_id() -> str:
        oid = _make_order_id(rng)
        while oid in used_order_ids:
            oid = _make_order_id(rng)
        used_order_ids.add(oid)
        return oid

    # ---- clean matches -----------------------------------------------
    for _ in range(n_clean):
        oid = fresh_order_id()
        amount = float(rng.choice(PRICE_POINTS))
        o = _build_order(rng, oid, amount)
        s = _build_settlement(rng, oid, amount, date.fromisoformat(o["order_date"]), delay_days=rng.randint(1, 3))
        orders.append(o); settlements.append(s)
        gt_orders[oid] = "CLEAN"; gt_settlements[s["settlement_id"]] = "CLEAN"

    # ---- delayed settlement (still matched, just slow) ----------------
    for _ in range(n_delayed):
        oid = fresh_order_id()
        amount = float(rng.choice(PRICE_POINTS))
        o = _build_order(rng, oid, amount)
        s = _build_settlement(rng, oid, amount, date.fromisoformat(o["order_date"]), delay_days=rng.randint(8, 15))
        orders.append(o); settlements.append(s)
        gt_orders[oid] = "DELAYED_SETTLEMENT"; gt_settlements[s["settlement_id"]] = "DELAYED_SETTLEMENT"

    # ---- amount mismatch (reference matches, amount doesn't) ----------
    for _ in range(n_amount_mismatch):
        oid = fresh_order_id()
        amount = float(rng.choice(PRICE_POINTS))
        settled_amount = round(amount - rng.choice([50, 99, 150, 200, 349, 500]), 2)
        o = _build_order(rng, oid, amount)
        s = _build_settlement(rng, oid, settled_amount, date.fromisoformat(o["order_date"]), delay_days=rng.randint(1, 3))
        orders.append(o); settlements.append(s)
        gt_orders[oid] = "AMOUNT_MISMATCH"; gt_settlements[s["settlement_id"]] = "AMOUNT_MISMATCH"

    # ---- fuzzy reference match (formatting drift, not a typo-typo) ----
    for _ in range(n_fuzzy_ref):
        oid = fresh_order_id()
        amount = float(rng.choice(PRICE_POINTS))
        o = _build_order(rng, oid, amount)
        drifted_id = _fuzzy_variant(oid, rng)
        s = _build_settlement(rng, drifted_id, amount, date.fromisoformat(o["order_date"]), delay_days=rng.randint(1, 3))
        orders.append(o); settlements.append(s)
        gt_orders[oid] = "FUZZY_REFERENCE_MATCH"; gt_settlements[s["settlement_id"]] = "FUZZY_REFERENCE_MATCH"

    # ---- unsettled order (no settlement at all) ------------------------
    for _ in range(n_unsettled):
        oid = fresh_order_id()
        amount = float(rng.choice(PRICE_POINTS))
        o = _build_order(rng, oid, amount)
        orders.append(o)
        gt_orders[oid] = "UNSETTLED_ORDER"

    # ---- unidentified settlement (no order at all) ----------------------
    for _ in range(n_unidentified):
        ghost_id = fresh_order_id()  # generated but never added to `orders`
        amount = float(rng.choice(PRICE_POINTS))
        s = _build_settlement(rng, ghost_id, amount, _random_order_date(rng), delay_days=rng.randint(1, 3))
        settlements.append(s)
        gt_settlements[s["settlement_id"]] = "UNIDENTIFIED_SETTLEMENT"

    # ---- duplicate settlement (customer retried after a false failure) --
    for _ in range(n_duplicate):
        oid = fresh_order_id()
        amount = float(rng.choice(PRICE_POINTS))
        o = _build_order(rng, oid, amount)
        order_date_obj = date.fromisoformat(o["order_date"])
        s1 = _build_settlement(rng, oid, amount, order_date_obj, delay_days=rng.randint(1, 3))
        s2 = _build_settlement(rng, oid, amount, order_date_obj, delay_days=rng.randint(4, 7))
        orders.append(o); settlements.append(s1); settlements.append(s2)
        gt_orders[oid] = "CLEAN"
        gt_settlements[s1["settlement_id"]] = "CLEAN"
        gt_settlements[s2["settlement_id"]] = "DUPLICATE_SETTLEMENT"

    rng.shuffle(orders)
    rng.shuffle(settlements)

    orders_csv = out_dir / "order_ledger.csv"
    with open(orders_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["order_id", "order_date", "customer_name", "channel", "order_amount", "order_status"])
        writer.writeheader()
        writer.writerows(orders)

    settlements_csv = out_dir / "settlement_report.csv"
    with open(settlements_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["settlement_id", "payment_id", "order_id", "method", "settled_at", "gross_amount", "fee", "tax", "settlement_utr"])
        writer.writeheader()
        writer.writerows(settlements)

    ground_truth = {
        "seed": seed,
        "merchant": MERCHANT_NAME,
        "note": "matcher.py never reads this file. validator.py uses it, after the fact, to grade the agent.",
        "orders": gt_orders,
        "settlements": gt_settlements,
    }
    (out_dir / "ground_truth.json").write_text(json.dumps(ground_truth, indent=2), encoding="utf-8")

    return len(orders), len(settlements)


if __name__ == "__main__":
    import sys
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent.parent / "data"
    n_o, n_s = generate(target)
    print(f"Wrote {n_o} orders and {n_s} settlements to {target}")
