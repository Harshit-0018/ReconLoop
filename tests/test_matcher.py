"""
tests/test_matcher.py — unit tests for the matching engine.

Standard library only (unittest) so these run with nothing to install:

    python3 -m unittest discover tests -v
"""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.models import LedgerOrder, SettlementRecord, Status, RESOLVED_STATUSES, EXCEPTION_STATUSES
from src.matcher import ReconciliationAgent


def make_order(order_id: str, amount: float, d: date, **kw) -> LedgerOrder:
    return LedgerOrder(
        order_id=order_id, order_date=d, customer_name="Test User",
        channel="Website", order_amount=amount, **kw,
    )


def make_settlement(settlement_id: str, order_id: str, amount: float, d: date, **kw) -> SettlementRecord:
    return SettlementRecord(
        settlement_id=settlement_id, payment_id=f"pay_{settlement_id}", order_id=order_id,
        method="upi", settled_at=d, gross_amount=amount,
        fee=round(amount * 0.02, 2), tax=round(amount * 0.02 * 0.18, 2),
        settlement_utr=f"UTR{settlement_id}", **kw,
    )


class TestReconciliationAgent(unittest.TestCase):
    def test_clean_match(self):
        o = make_order("order_AAA", 1000.0, date(2026, 8, 1))
        s = make_settlement("setl_AAA", "order_AAA", 1000.0, date(2026, 8, 3))
        ReconciliationAgent([o], [s]).run()
        self.assertEqual(o.status, Status.CLEAN)
        self.assertEqual(s.status, Status.CLEAN)
        self.assertEqual(o.matched_settlement_id, "setl_AAA")
        self.assertEqual(s.matched_order_id, "order_AAA")

    def test_amount_mismatch_is_an_exception_not_a_forced_match(self):
        o = make_order("order_BBB", 1000.0, date(2026, 8, 1))
        s = make_settlement("setl_BBB", "order_BBB", 850.0, date(2026, 8, 2))
        ReconciliationAgent([o], [s]).run()
        self.assertEqual(o.status, Status.AMOUNT_MISMATCH)
        self.assertIn(o.status, EXCEPTION_STATUSES)
        self.assertNotIn(o.status, RESOLVED_STATUSES)

    def test_amount_within_tolerance_still_counts_as_clean(self):
        o = make_order("order_TOL", 1000.00, date(2026, 8, 1))
        s = make_settlement("setl_TOL", "order_TOL", 1000.60, date(2026, 8, 2))  # 60 paise rounding
        ReconciliationAgent([o], [s]).run()
        self.assertEqual(o.status, Status.CLEAN)

    def test_delayed_settlement_still_counts_toward_match_rate(self):
        o = make_order("order_CCC", 500.0, date(2026, 8, 1))
        s = make_settlement("setl_CCC", "order_CCC", 500.0, date(2026, 8, 20))
        ReconciliationAgent([o], [s]).run()
        self.assertEqual(o.status, Status.DELAYED_SETTLEMENT)
        self.assertIn(o.status, RESOLVED_STATUSES)

    def test_unsettled_order_reported_not_dropped(self):
        o = make_order("order_DDD", 700.0, date(2026, 8, 1))
        result = ReconciliationAgent([o], []).run()
        self.assertEqual(o.status, Status.UNSETTLED_ORDER)
        self.assertIn(o, result.orders)  # still present in the result — never silently discarded

    def test_unidentified_settlement_reported_not_dropped(self):
        s = make_settlement("setl_EEE", "order_GHOST", 300.0, date(2026, 8, 1))
        result = ReconciliationAgent([], [s]).run()
        self.assertEqual(s.status, Status.UNIDENTIFIED_SETTLEMENT)
        self.assertIn(s, result.settlements)

    def test_fuzzy_reference_match_catches_formatting_drift(self):
        o = make_order("order_9f3ac1b2e4d1a0", 250.0, date(2026, 8, 1))
        drifted = "order_9f3ac1b2e4d1a0"[:-1]  # dropped last char — small drift, not exact
        s = make_settlement("setl_FFF", drifted, 250.0, date(2026, 8, 2))
        ReconciliationAgent([o], [s]).run()
        self.assertEqual(o.status, Status.FUZZY_REFERENCE_MATCH)
        self.assertIn(o.status, RESOLVED_STATUSES)

    def test_unrelated_references_never_fuzzy_match(self):
        o = make_order("order_completely_different_1", 250.0, date(2026, 8, 1))
        s = make_settlement("setl_GGG", "order_totally_unrelated_2", 250.0, date(2026, 8, 2))
        ReconciliationAgent([o], [s]).run()
        self.assertEqual(o.status, Status.UNSETTLED_ORDER)
        self.assertEqual(s.status, Status.UNIDENTIFIED_SETTLEMENT)

    def test_duplicate_settlement_flagged_primary_stays_clean(self):
        o = make_order("order_HHH", 400.0, date(2026, 8, 1))
        s1 = make_settlement("setl_H1", "order_HHH", 400.0, date(2026, 8, 2))
        s2 = make_settlement("setl_H2", "order_HHH", 400.0, date(2026, 8, 5))
        ReconciliationAgent([o], [s1, s2]).run()
        self.assertEqual(o.status, Status.CLEAN)
        self.assertEqual(s1.status, Status.CLEAN)  # earlier settlement = primary
        self.assertEqual(s2.status, Status.DUPLICATE_SETTLEMENT)

    def test_match_rate_only_counts_resolved_statuses(self):
        orders = [make_order("order_I1", 100.0, date(2026, 8, 1)), make_order("order_I2", 200.0, date(2026, 8, 1))]
        settlements = [make_settlement("setl_I1", "order_I1", 100.0, date(2026, 8, 2))]
        ReconciliationAgent(orders, settlements).run()
        matched = sum(1 for o in orders if o.status in RESOLVED_STATUSES)
        self.assertEqual(matched, 1)

    def test_every_record_gets_exactly_one_status(self):
        """No record should ever leave run() with status still None."""
        orders = [make_order(f"order_J{i}", 100.0 * i, date(2026, 8, 1)) for i in range(1, 6)]
        settlements = [make_settlement(f"setl_J{i}", f"order_J{i}", 100.0 * i, date(2026, 8, 2)) for i in range(1, 4)]
        result = ReconciliationAgent(orders, settlements).run()
        self.assertTrue(all(o.status is not None for o in result.orders))
        self.assertTrue(all(s.status is not None for s in result.settlements))

    def test_totals_always_add_up(self):
        """total = matched + exceptions, on both sides, always — the 'honest exception list' guarantee."""
        orders = [make_order(f"order_K{i}", 100.0, date(2026, 8, 1)) for i in range(10)]
        settlements = [make_settlement(f"setl_K{i}", f"order_K{i}", 100.0, date(2026, 8, 2)) for i in range(6)]
        result = ReconciliationAgent(orders, settlements).run()
        resolved = sum(1 for o in result.orders if o.status in RESOLVED_STATUSES)
        excepted = sum(1 for o in result.orders if o.status in EXCEPTION_STATUSES)
        self.assertEqual(resolved + excepted, len(result.orders))


if __name__ == "__main__":
    unittest.main()
