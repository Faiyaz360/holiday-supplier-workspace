"""ADK web entry point. Scope is trusted launch configuration, never a chat argument."""

import os
from datetime import date
from pathlib import Path

from reconciliation.adk_agent import build_agent
from reconciliation.core import load_ledger, load_statement, reconcile
from reconciliation.fixtures import SUPPLIERS
from reconciliation.tools import FinanceClient, ReconciliationTools

root = Path(__file__).resolve().parents[1]
supplier = os.getenv("DEMO_SUPPLIER_ID", "SUP-PMI")
if supplier not in SUPPLIERS:
    raise ValueError("Choose a documented fictional supplier ID")
statement = load_statement(root / "data" / "statements" / f"{supplier}.csv")
ledger = load_ledger(root / "data" / "ledger.csv", date.fromisoformat(statement["cutoff_date"]))
finance = (
    FinanceClient(os.environ["DEMO_API_BASE_URL"], os.getenv("DEMO_API_TOKEN", ""), supplier)
    if os.getenv("DEMO_API_BASE_URL")
    else None
)
root_agent = build_agent(ReconciliationTools({"result": reconcile(statement, ledger)}, finance))
