"""
app.py — optional interactive dashboard for ReconLoop.

Not required to demo the project — output/report.html (written by
`python3 main.py`) is the zero-dependency, guaranteed-to-work path. This
is a nicer, interactive alternative if you have Streamlit installed:

    pip install -r requirements.txt
    streamlit run app.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.data_generator import generate, DEFAULT_SEED
from src.data_loader import load_orders, load_settlements
from src.matcher import ReconciliationAgent
from src.report import build_summary
from src.validator import validate
from src.models import RESOLVED_STATUSES, EXCEPTION_STATUSES, FLAG_STATUSES

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"

st.set_page_config(page_title="ReconLoop — AI Finance Controller", page_icon="📒", layout="wide")

st.markdown(
    """
    <style>
    .stApp { background-color: #EFE8D8; }
    h1, h2, h3 { font-family: Georgia, 'Fraunces', serif !important; }
    div[data-testid="stMetric"] {
        background: #E6DEC9; border: 1px solid #C9BFA0; border-radius: 3px; padding: 0.6rem 0.8rem;
    }
    code, .stCode { font-family: 'IBM Plex Mono', ui-monospace, monospace !important; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("📒 ReconLoop — AI Finance Controller")
st.caption("Razorpay AI Buildathon 2026 · Track 04 · Multi-source reconciliation, not a black box")

with st.sidebar:
    st.header("Run controls")
    seed = st.number_input("Synthetic data seed", value=DEFAULT_SEED, step=1)
    regenerate = st.button("Regenerate synthetic data")
    use_llm = st.toggle(
        "Narrate exceptions with Claude",
        value=False,
        help="Needs ANTHROPIC_API_KEY in your environment. The LLM only rewords a note the "
             "deterministic agent already wrote — it never makes a matching decision.",
    )
    st.divider()
    st.caption(
        "The matching decisions above are 100% rule-based (see src/matcher.py). "
        "This toggle only affects the wording of exception notes."
    )

DATA_DIR.mkdir(exist_ok=True)
orders_csv = DATA_DIR / "order_ledger.csv"
settlements_csv = DATA_DIR / "settlement_report.csv"

if regenerate or not orders_csv.exists():
    generate(DATA_DIR, seed=int(seed))

orders = load_orders(orders_csv)
settlements = load_settlements(settlements_csv)

start = time.perf_counter()
agent = ReconciliationAgent(orders, settlements)
result = agent.run()
elapsed = time.perf_counter() - start

if use_llm:
    from src.explainer import explain_all
    explain_all([r for r in (orders + settlements) if r.status is not None], use_llm=True)

validation = validate(orders, settlements, DATA_DIR / "ground_truth.json")
summary = build_summary(orders, settlements, elapsed, validation)

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Order match rate", f"{summary['order_match_rate_percent']}%")
c2.metric("Settlement match rate", f"{summary['settlement_match_rate_percent']}%")
c3.metric("Exceptions", summary["exceptions_count"])
rps = summary["throughput"]["records_per_second"]
c4.metric("Throughput", f"{rps:.0f}/s" if rps is not None else "n/a")
c5.metric("Accuracy vs ground truth", f"{validation['accuracy_percent']}%" if validation else "n/a")

st.divider()

with st.expander("🔍 Agent reasoning trail", expanded=True):
    for i, line in enumerate(result.trace, 1):
        st.markdown(f"**{i}.** {line}")

st.divider()

exceptions = [o for o in orders if o.status in EXCEPTION_STATUSES] + \
    [s for s in settlements if s.status in EXCEPTION_STATUSES]
flags = [o for o in orders if o.status in FLAG_STATUSES] + \
    [s for s in settlements if s.status in FLAG_STATUSES]
matched = [o for o in orders if o.status in RESOLVED_STATUSES]


def _row(r):
    rid = getattr(r, "order_id", None) or getattr(r, "settlement_id", None)
    amount = getattr(r, "order_amount", None)
    if amount is None:
        amount = getattr(r, "gross_amount", None)
    return {"Reference": rid, "Status": r.status.value, "Amount (₹)": amount, "Note": r.notes}


st.subheader(f"⚠️ Exceptions ({len(exceptions)}) — every one, not cherry-picked")
if exceptions:
    st.dataframe([_row(r) for r in exceptions], use_container_width=True, hide_index=True)
else:
    st.success("No exceptions in this batch.")

with st.expander(f"🚩 Flagged matches ({len(flags)}) — resolved, but worth a look"):
    st.dataframe([_row(r) for r in flags], use_container_width=True, hide_index=True)

with st.expander(f"✅ Matched orders ({len(matched)})"):
    st.dataframe(
        [
            {"Order": o.order_id, "Customer": o.customer_name, "Amount (₹)": o.order_amount, "Status": o.status.value}
            for o in matched
        ],
        use_container_width=True, hide_index=True,
    )

st.caption(
    f"Generated {summary['generated_at']} · {summary['total_records_processed']} records processed "
    f"in {summary['throughput']['elapsed_seconds']}s"
)
