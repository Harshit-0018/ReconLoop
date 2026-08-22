"""
data_loader.py — reads the two source CSVs into typed dataclasses.

Kept deliberately dumb: no matching logic lives here, only parsing. If a
row is malformed we fail loudly (a reconciliation tool that silently drops
a bad row is worse than one that crashes) rather than guessing.
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
from typing import List, Union

from .models import LedgerOrder, SettlementRecord


def _parse_date(value: str) -> date:
    return date.fromisoformat(value.strip())


def load_orders(path: Union[str, Path]) -> List[LedgerOrder]:
    orders: List[LedgerOrder] = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            orders.append(
                LedgerOrder(
                    order_id=row["order_id"].strip(),
                    order_date=_parse_date(row["order_date"]),
                    customer_name=row["customer_name"].strip(),
                    channel=row["channel"].strip(),
                    order_amount=float(row["order_amount"]),
                    order_status=row.get("order_status", "Completed").strip(),
                )
            )
    return orders


def load_settlements(path: Union[str, Path]) -> List[SettlementRecord]:
    settlements: List[SettlementRecord] = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            settlements.append(
                SettlementRecord(
                    settlement_id=row["settlement_id"].strip(),
                    payment_id=row["payment_id"].strip(),
                    order_id=row["order_id"].strip(),
                    method=row["method"].strip(),
                    settled_at=_parse_date(row["settled_at"]),
                    gross_amount=float(row["gross_amount"]),
                    fee=float(row["fee"]),
                    tax=float(row["tax"]),
                    settlement_utr=row["settlement_utr"].strip(),
                )
            )
    return settlements
