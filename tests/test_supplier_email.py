from reconciliation.report import render_supplier_email


def test_email_preserves_every_exception_line_currency_and_missing_side():
    rows = [
        {
            "id": "R-1",
            "invoice_ref": "INV-1",
            "status": "amount_mismatch",
            "statement_lines": [{"currency": "EUR", "claimed_amount": "239.00"}],
            "ledger_lines": [{"currency": "EUR", "outstanding_amount": "214.00"}],
        },
        {
            "id": "R-2",
            "invoice_ref": "INV-2",
            "status": "currency_mismatch",
            "statement_lines": [{"currency": "GBP", "claimed_amount": "221.00"}],
            "ledger_lines": [{"currency": "EUR", "outstanding_amount": "221.00"}],
        },
        {
            "id": "R-3",
            "invoice_ref": "INV-3",
            "status": "ambiguous",
            "statement_lines": [{"currency": "EUR", "claimed_amount": "228.00"}] * 2,
            "ledger_lines": [{"currency": "EUR", "outstanding_amount": "228.00"}],
        },
        {
            "id": "R-4",
            "invoice_ref": "INV-4",
            "status": "statement_only",
            "statement_lines": [{"currency": "EUR", "claimed_amount": "0.00"}],
            "ledger_lines": [],
        },
        {
            "id": "R-5",
            "invoice_ref": "INV-5",
            "status": "ledger_only",
            "statement_lines": [],
            "ledger_lines": [{"currency": "EUR", "outstanding_amount": "235.00"}],
        },
        {
            "id": "R-6",
            "invoice_ref": "INV-MATCHED",
            "status": "matched",
            "statement_lines": [],
            "ledger_lines": [],
        },
    ]
    import copy

    result = {"statement_id": "ST-1", "demo": False, "rows": rows}
    original = copy.deepcopy(result)
    text = render_supplier_email(result)
    assert "Subject: Invoice queries" in text and "Hello," in text
    assert "sample ledger" in text and "No message has been sent" in text
    for ref in ["INV-1", "INV-2", "INV-3", "INV-4", "INV-5"]:
        assert ref in text
    assert "INV-MATCHED" not in text
    assert "Statement: GBP 221.00" in text and "Our ledger: EUR 221.00" in text
    assert "Statement: EUR 228.00; EUR 228.00" in text
    assert "Statement: EUR 0.00" in text
    assert "Our ledger: Not recorded" in text and "Statement: Not recorded" in text
    assert "R-1 |" not in text and "CSV row" not in text
    assert result == original


def test_empty_email_does_not_invent_a_query_or_claim_payment():
    text = render_supplier_email({"statement_id": "ST-0", "rows": [], "demo": True})
    assert "No invoice differences" in text
    assert "Fictional" in text and "authorise payment" in text


def test_read_adds_email_without_rewriting_saved_query_or_review_evidence():
    from test_hosted_api import EXECUTE, READ, auth, client

    http, workflow = client()
    run_id = http.post("/runs", json={}, headers=auth(EXECUTE)).json()["run_id"]
    original = workflow.read(run_id)
    data = http.get(f"/runs/{run_id}", headers=auth(READ)).json()
    assert data["supplier_email"] == render_supplier_email(data["result"])
    assert "Subject: Invoice queries" in data["supplier_email"]
    assert "DRAFT FOR HUMAN REVIEW" in data["draft_text"]
    assert workflow.read(run_id) == original
    assert http.get(f"/runs/{run_id}").status_code == 401
