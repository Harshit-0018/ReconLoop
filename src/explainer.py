"""
explainer.py — turns agent decisions into notes a human can read.

Two modes:
  - Rule-based (default, always on): matcher.py already attaches a specific
    `.notes` string to every record when it decides that record's status.
    This module's rule-based path is just that — it's already done.
  - LLM-enhanced (optional, off by default, `--use-llm`): if
    ANTHROPIC_API_KEY is set, each exception/flag note is rewritten in a
    slightly more narrative style by Claude. The matching decision itself
    was already made by matcher.py before this module ever runs — the LLM
    only rewords a note, it never sees the raw ledger or gets a vote on
    what counts as a match. If the call fails for any reason (no key, no
    package, network, rate limit), we silently fall back to the rule-based
    note so the report never breaks because of this optional step.
"""

from __future__ import annotations

import os
from typing import List, Union

from .models import LedgerOrder, SettlementRecord

# Haiku, not a bigger model: this call only narrates a decision matcher.py
# already made, it doesn't need to reason about anything — fast and cheap
# is the right trade-off here.
CLAUDE_MODEL = "claude-haiku-4-5-20251001"

Record = Union[LedgerOrder, SettlementRecord]


def _record_id(record: Record) -> str:
    return getattr(record, "order_id", None) or getattr(record, "settlement_id", "")


def _llm_rewrite(record: Record) -> str:
    """Best-effort natural-language rewrite of an already-decided note.
    Returns the original note unchanged on any failure."""
    original = record.notes
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return original
    try:
        import anthropic  # optional dependency — only imported if actually used

        client = anthropic.Anthropic(api_key=api_key)
        prompt = (
            "You are annotating one line of a finance reconciliation exception report for a "
            "finance-ops reviewer. Rewrite the note below in 1-2 plain sentences. Do not invent "
            "numbers or facts that aren't already in the note. Do not soften the finding — this "
            "is an audit trail, not marketing copy. Reply with only the rewritten note.\n\n"
            f"Record: {_record_id(record)}\n"
            f"Status: {record.status.value if record.status else 'UNKNOWN'}\n"
            f"Original note: {original}"
        )
        message = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=120,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(block.text for block in message.content if getattr(block, "type", "") == "text").strip()
        return text or original
    except Exception:
        # Any failure (missing package, bad key, network, rate limit) — the
        # report must still ship with the rule-based note.
        return original


def explain_all(records: List[Record], use_llm: bool = False) -> None:
    """Mutates `.notes` on each record in place. With use_llm=False (the
    default) this is a no-op — matcher.py's notes are used as-is."""
    if not use_llm:
        return
    for record in records:
        record.notes = _llm_rewrite(record)
