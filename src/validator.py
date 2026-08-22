"""
validator.py — grades the agent's output against known ground truth.

This is where "measured accuracy" (as opposed to a claimed one) comes from:
because the synthetic batch was generated deliberately, we know exactly
which category every record was engineered to fall into
(data/ground_truth.json). This module is the ONLY place that file is read —
matcher.py never sees it, so the agent can't "cheat" by looking up the
answer key, and the accuracy number below reflects the matching logic
actually working, not memorized answers.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional, Union

from .models import LedgerOrder, SettlementRecord


def validate(
    orders: List[LedgerOrder],
    settlements: List[SettlementRecord],
    ground_truth_path: Union[str, Path],
) -> Optional[dict]:
    """Returns None if no ground truth file is present (e.g. on a real,
    non-synthetic batch) — validation is a bonus, not a requirement to run."""
    gt_path = Path(ground_truth_path)
    if not gt_path.exists():
        return None

    ground_truth = json.loads(gt_path.read_text(encoding="utf-8"))
    gt_orders: dict = ground_truth.get("orders", {})
    gt_settlements: dict = ground_truth.get("settlements", {})

    mismatches = []
    correct = 0
    total = 0

    for order in orders:
        expected = gt_orders.get(order.order_id)
        if expected is None:
            continue
        total += 1
        actual = order.status.value if order.status else None
        if actual == expected:
            correct += 1
        else:
            mismatches.append({"side": "order", "id": order.order_id, "expected": expected, "actual": actual})

    for settlement in settlements:
        expected = gt_settlements.get(settlement.settlement_id)
        if expected is None:
            continue
        total += 1
        actual = settlement.status.value if settlement.status else None
        if actual == expected:
            correct += 1
        else:
            mismatches.append({
                "side": "settlement", "id": settlement.settlement_id, "expected": expected, "actual": actual,
            })

    return {
        "ground_truth_records": total,
        "correctly_classified": correct,
        "accuracy_percent": round(correct / total * 100, 2) if total else None,
        "misclassified": mismatches,
    }
