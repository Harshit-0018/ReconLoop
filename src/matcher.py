"""
matcher.py — the reconciliation agent's matching engine.

Deterministic, multi-pass, and fully explainable on purpose: for a finance
decision, "the model said so" is not an audit trail. Every match or
exception below traces back to a rule you can point to, and every record
gets a human-readable note explaining why it landed where it did.

matcher.py has NO knowledge of data/ground_truth.json. It only ever sees
LedgerOrder and SettlementRecord objects loaded from the two CSVs, exactly
like a real reconciliation job would.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .models import LedgerOrder, SettlementRecord, Status, RESOLVED_STATUSES

AMOUNT_TOLERANCE = 1.00              # ₹1 — covers float rounding only, never a real discrepancy
NORMAL_SETTLEMENT_WINDOW_DAYS = 4    # Razorpay's standard domestic cycle is T+2; we allow to T+4 before flagging
FUZZY_REF_THRESHOLD = 0.85           # difflib similarity ratio required for a fuzzy reference match


@dataclass
class AgentResult:
    orders: List[LedgerOrder]
    settlements: List[SettlementRecord]
    trace: List[str] = field(default_factory=list)


class ReconciliationAgent:
    """Closes the loop between an internal order ledger and a payment
    gateway settlement report. Runs in ordered passes, logging its own
    reasoning at each step so the trail can be shown to a human."""

    def __init__(self, orders: List[LedgerOrder], settlements: List[SettlementRecord]):
        self.orders = orders
        self.settlements = settlements
        self.trace: List[str] = []
        self._order_by_id: Dict[str, LedgerOrder] = {o.order_id.strip(): o for o in orders}

    def run(self) -> AgentResult:
        self.trace.append(
            f"Loaded {len(self.orders)} ledger orders and {len(self.settlements)} settlement records."
        )

        by_order_id: Dict[str, List[SettlementRecord]] = {}
        for s in self.settlements:
            by_order_id.setdefault(s.order_id.strip(), []).append(s)

        consumed_settlement_ids: set = set()

        # ---------------- Pass 1: exact order_id match ----------------
        p1 = {"CLEAN": 0, "DELAYED": 0, "MISMATCH": 0}
        for order in self.orders:
            group = sorted(by_order_id.get(order.order_id.strip(), []), key=lambda s: s.settled_at)
            if not group:
                continue
            primary = group[0]  # earliest settlement for this reference is the primary match
            self._resolve_pair(order, primary, fuzzy=False)
            consumed_settlement_ids.add(primary.settlement_id)
            if order.status == Status.CLEAN:
                p1["CLEAN"] += 1
            elif order.status == Status.DELAYED_SETTLEMENT:
                p1["DELAYED"] += 1
            elif order.status == Status.AMOUNT_MISMATCH:
                p1["MISMATCH"] += 1

        self.trace.append(
            f"Pass 1 (exact order_id match): {p1['CLEAN']} clean, {p1['DELAYED']} matched-but-delayed, "
            f"{p1['MISMATCH']} amount mismatches held as exceptions (not force-matched)."
        )

        # ---------------- Pass 2: fuzzy order_id match -----------------
        all_order_ids = set(self._order_by_id.keys())
        unmatched_orders = [o for o in self.orders if o.status is None]
        # Only offer settlements whose order_id doesn't exactly match ANY ledger
        # order to the fuzzy pool — same-key extras belong to Pass 3 (duplicates).
        fuzzy_pool = [
            s for s in self.settlements
            if s.settlement_id not in consumed_settlement_ids and s.order_id.strip() not in all_order_ids
        ]

        p2_resolved = 0
        for order in unmatched_orders:
            best: Optional[SettlementRecord] = None
            best_ratio = 0.0
            for s in fuzzy_pool:
                if s.settlement_id in consumed_settlement_ids:
                    continue
                ratio = difflib.SequenceMatcher(None, order.order_id.strip(), s.order_id.strip()).ratio()
                if ratio > best_ratio:
                    best_ratio, best = ratio, s
            if best is not None and best_ratio >= FUZZY_REF_THRESHOLD:
                self._resolve_pair(order, best, fuzzy=True)
                consumed_settlement_ids.add(best.settlement_id)
                p2_resolved += 1

        self.trace.append(
            f"Pass 2 (fuzzy order_id match, similarity >= {FUZZY_REF_THRESHOLD}): "
            f"resolved {p2_resolved} more order(s) whose reference had formatting drift."
        )

        # ---------------- Pass 3: duplicate settlement detection --------
        duplicates = 0
        for s in self.settlements:
            if s.status is not None:
                continue
            owner = self._order_by_id.get(s.order_id.strip())
            if owner is not None and owner.status is not None:
                s.status = Status.DUPLICATE_SETTLEMENT
                s.matched_order_id = owner.order_id
                s.notes = (
                    f"Extra settlement for {owner.order_id}, which was already matched to "
                    f"{owner.matched_settlement_id}. Looks like the payment was retried after a false failure — "
                    f"worth checking whether a refund is owed."
                )
                duplicates += 1

        self.trace.append(
            f"Pass 3 (duplicate detection): flagged {duplicates} extra settlement(s) beyond what each order needed."
        )

        # ---------------- Whatever's left is reported, not discarded ----
        unsettled = 0
        for order in self.orders:
            if order.status is None:
                order.status = Status.UNSETTLED_ORDER
                order.notes = (
                    f"No settlement record found for order_id '{order.order_id}', by exact or fuzzy match. "
                    f"Either the payment hasn't settled yet, or it failed silently on the customer's end."
                )
                unsettled += 1

        unidentified = 0
        for s in self.settlements:
            if s.status is None:
                s.status = Status.UNIDENTIFIED_SETTLEMENT
                s.notes = (
                    f"Settlement {s.settlement_id} (order_id '{s.order_id}') has no matching order in the ledger. "
                    f"Could be a manually-created payment link, a test transaction, or a reference typo on the order side."
                )
                unidentified += 1

        self.trace.append(
            f"Remaining after all passes: {unsettled} order(s) with no settlement at all, "
            f"{unidentified} settlement(s) with no matching order at all — reported as exceptions, not hidden."
        )

        matched_orders = sum(1 for o in self.orders if o.status in RESOLVED_STATUSES)
        matched_settlements = sum(1 for s in self.settlements if s.status in RESOLVED_STATUSES)
        order_rate = (matched_orders / len(self.orders) * 100) if self.orders else 0.0
        settlement_rate = (matched_settlements / len(self.settlements) * 100) if self.settlements else 0.0
        self.trace.append(
            f"Final: {matched_orders}/{len(self.orders)} orders matched ({order_rate:.1f}%), "
            f"{matched_settlements}/{len(self.settlements)} settlements matched ({settlement_rate:.1f}%)."
        )

        return AgentResult(orders=self.orders, settlements=self.settlements, trace=self.trace)

    def _resolve_pair(self, order: LedgerOrder, settlement: SettlementRecord, fuzzy: bool) -> None:
        diff = abs(order.order_amount - settlement.gross_amount)
        gap_days = (settlement.settled_at - order.order_date).days

        order.matched_settlement_id = settlement.settlement_id
        settlement.matched_order_id = order.order_id

        if diff > AMOUNT_TOLERANCE:
            note = (
                f"Reference matched to {settlement.settlement_id}, but amounts disagree: "
                f"ledger shows ₹{order.order_amount:,.2f} while the gateway settled ₹{settlement.gross_amount:,.2f} "
                f"(difference of ₹{diff:,.2f}). Usually a discount, partial refund, or manual correction applied on "
                f"only one side."
            )
            order.status = settlement.status = Status.AMOUNT_MISMATCH
            order.notes = settlement.notes = note
            return

        if fuzzy:
            status, note = Status.FUZZY_REFERENCE_MATCH, (
                f"Matched via approximate reference text (order_id '{order.order_id}' vs settlement order_id "
                f"'{settlement.order_id}') — formatting differs slightly. Recommend verifying manually."
            )
        elif gap_days > NORMAL_SETTLEMENT_WINDOW_DAYS:
            status, note = Status.DELAYED_SETTLEMENT, (
                f"Matched, but settled {gap_days} days after the order date "
                f"(normal window is {NORMAL_SETTLEMENT_WINDOW_DAYS} days). Still resolved — just a slower cycle."
            )
        else:
            status, note = Status.CLEAN, "Matched — reference, amount, and settlement window all check out."

        order.status = settlement.status = status
        order.notes = settlement.notes = note
