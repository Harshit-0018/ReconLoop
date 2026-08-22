"""
models.py — data models for ReconLoop (AI Finance Controller).

Two record types come in from two independent sources:
  - LedgerOrder      : what our own system recorded when the customer paid
  - SettlementRecord : what the payment gateway actually settled to the bank

The agent's job is to line these up and be honest about what it can't.

Status is the single source of truth the whole project is built from: order
match rate, settlement match rate, the exception list, and the accuracy
validation are all just different ways of counting these labels. Nothing
else decides what's "matched" — one enum, one place.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Optional


class Status(str, Enum):
    """Every ledger order and settlement record ends up with exactly one
    of these once the agent has run."""

    CLEAN = "CLEAN"                                     # resolved, no notes
    DELAYED_SETTLEMENT = "DELAYED_SETTLEMENT"           # resolved, flagged: slow settlement
    FUZZY_REFERENCE_MATCH = "FUZZY_REFERENCE_MATCH"     # resolved, flagged: reference text drifted
    AMOUNT_MISMATCH = "AMOUNT_MISMATCH"                 # exception: found the pair, amounts disagree
    UNSETTLED_ORDER = "UNSETTLED_ORDER"                 # exception: order side has no settlement at all
    UNIDENTIFIED_SETTLEMENT = "UNIDENTIFIED_SETTLEMENT"  # exception: settlement side has no order at all
    DUPLICATE_SETTLEMENT = "DUPLICATE_SETTLEMENT"       # exception: extra settlement beyond the one needed


# A record counts toward the match rate if and only if its status is in here.
RESOLVED_STATUSES = {Status.CLEAN, Status.DELAYED_SETTLEMENT, Status.FUZZY_REFERENCE_MATCH}

# Resolved, but worth a human glance — still counted as matched.
FLAG_STATUSES = {Status.DELAYED_SETTLEMENT, Status.FUZZY_REFERENCE_MATCH}

# Could not be auto-resolved. These are what "the exception list" means in this project.
EXCEPTION_STATUSES = {
    Status.AMOUNT_MISMATCH,
    Status.UNSETTLED_ORDER,
    Status.UNIDENTIFIED_SETTLEMENT,
    Status.DUPLICATE_SETTLEMENT,
}

assert RESOLVED_STATUSES | EXCEPTION_STATUSES == set(Status), "every status must be either resolved or an exception"


@dataclass
class LedgerOrder:
    """One row of our internal order ledger — the 'what we think happened' side."""

    order_id: str          # e.g. order_9f3ac1b2e4d1a0 — Razorpay-style order reference
    order_date: date
    customer_name: str
    channel: str            # Website / Mobile App / Marketplace
    order_amount: float     # INR, gross
    order_status: str = "Completed"

    # filled in by ReconciliationAgent.run() — untouched until then
    status: Optional[Status] = None
    matched_settlement_id: Optional[str] = None
    notes: str = ""


@dataclass
class SettlementRecord:
    """One row of the payment gateway's settlement report — the 'what actually
    landed in the bank' side."""

    settlement_id: str      # e.g. setl_7c2b9a1d44e1 — settlement batch id
    payment_id: str         # e.g. pay_4e1a8c3f9b02aa — individual payment id
    order_id: str            # the reference the gateway received — should match a LedgerOrder.order_id
    method: str               # upi / card / netbanking / wallet
    settled_at: date
    gross_amount: float
    fee: float
    tax: float               # GST on the fee, per Razorpay's settlement fields
    settlement_utr: str

    # filled in by ReconciliationAgent.run() — untouched until then
    status: Optional[Status] = None
    matched_order_id: Optional[str] = None
    notes: str = ""

    @property
    def net_amount(self) -> float:
        """What actually hit the bank account, after fee + tax deductions."""
        return round(self.gross_amount - self.fee - self.tax, 2)
