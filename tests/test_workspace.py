import base64
import hashlib
import re
from pathlib import Path

from reconciliation.core import reconcile_files
from reconciliation.report import render_report

ROOT = Path(__file__).resolve().parents[1]


def report():
    facts = reconcile_files(ROOT / "data/statements/SUP-PMI.csv", ROOT / "data/ledger.csv")
    return render_report(
        facts, {"run_id": "abc", "drafting_status": "offline_template", "review": None}
    )


def test_missing_due_date_is_shown_as_unknown_in_invoice_evidence():
    html = report()
    assert "Due date unknown" in html
    assert "Not overdue" not in html


def test_standalone_workspace_allows_only_its_exact_script():
    html = report()
    script = re.search(r"<script>(.*?)</script>", html, re.S)
    assert script, "Interactive invoice search is missing"
    digest = base64.b64encode(hashlib.sha256(script[1].encode()).digest()).decode()
    assert f"script-src 'sha256-{digest}'" in html
    assert "script-src 'unsafe-inline'" not in html
    assert "https://" not in html, "Standalone report must not call external services"


def test_review_workspace_keeps_complete_matched_and_exception_evidence():
    html = report()
    assert 'aria-label="Search invoices"' in html
    assert 'id="invoice-filter"' in html
    assert 'id="copy-draft"' in html
    assert 'id="download-draft"' in html
    assert 'data-status="matched"' in html
    assert 'data-status="statement_only"' in html
    assert "EUR 239.00" in html and "EUR 214.00" in html
    assert "Fictional travel records" in html
    assert "Independent application demonstration" in html


def test_partially_settled_invoice_shows_original_and_remaining_balances():
    html = report()
    assert "Original invoice: EUR 207.00" in html
    assert "EUR 157.00" in html
