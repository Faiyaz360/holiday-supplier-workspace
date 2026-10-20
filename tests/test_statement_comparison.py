from reconciliation.statement_comparison import compare_statements


def payload(rows, supplier="SUP-TEST", run_id="r"):
    return {
        "meta": {"run_id": run_id},
        "revision": 1,
        "result": {
            "supplier_id": supplier,
            "statement_id": run_id,
            "cutoff_date": "2026-01-01",
            "rows": rows,
        },
    }


def row(ref, amount="10.00", status="matched", booking="B1", currency="GBP"):
    return {
        "invoice_ref": ref,
        "id": "evidence-id",
        "status": status,
        "statement_lines": [
            {"currency": currency, "claimed_amount": amount, "booking_ref": booking}
        ],
        "ledger_lines": [],
    }


def test_order_insensitive_and_duplicate_lines():
    a = row("A")
    a["statement_lines"].append({"currency": "GBP", "claimed_amount": "2", "booking_ref": "B2"})
    b = row("A")
    b["statement_lines"].append({"currency": "GBP", "claimed_amount": "2", "booking_ref": "B2"})
    assert compare_statements(payload([a]), payload([b]))["counts"] == {
        "new": 0,
        "removed": 0,
        "changed": 0,
        "unchanged": 1,
        "still_needs_review": 0,
    }


def test_amount_currency_missing_and_review():
    before = [row("A", status="unmatched"), row("OLD")]
    after = [row("A", amount="11", status="unmatched", currency="EUR"), row("NEW")]
    result = compare_statements(payload(before, run_id="old"), payload(after, run_id="new"))
    assert result["counts"] == {
        "new": 1,
        "removed": 1,
        "changed": 1,
        "unchanged": 0,
        "still_needs_review": 1,
    }
    assert result["rows"][0]["invoice_ref"] == "A"


def test_does_not_mutate_and_rejects_supplier_mismatch():
    before = payload([row("A")])
    current = payload([row("A")])
    snapshot = repr(before)
    assert compare_statements(before, current)
    assert repr(before) == snapshot
    try:
        compare_statements(before, payload([], supplier="OTHER"))
    except ValueError as error:
        assert "same supplier" in str(error)
    else:
        raise AssertionError("supplier mismatch accepted")


def test_ledger_only_is_removed_and_decimal_format_is_equal():
    old = row("A", amount="239.0")
    current = {
        "invoice_ref": "A",
        "id": "new",
        "status": "unmatched",
        "statement_lines": [],
        "ledger_lines": [{"currency": "GBP", "outstanding_amount": "239.00", "booking_ref": "B1"}],
    }
    result = compare_statements(payload([old]), payload([current]))
    assert result["rows"][0]["change"] == "removed"


def test_source_swap_is_changed():
    old = row("A")
    current = {
        "invoice_ref": "A",
        "status": "matched",
        "statement_lines": [],
        "ledger_lines": [{"currency": "GBP", "outstanding_amount": "10.00", "booking_ref": "B1"}],
    }
    assert compare_statements(payload([old]), payload([current]))["rows"][0]["change"] == "removed"


def test_existing_ledger_only_invoice_is_not_new_to_statement():
    item = {
        "invoice_ref": "A",
        "id": "R-0001",
        "status": "ledger_only",
        "statement_lines": [],
        "ledger_lines": [{"currency": "EUR", "outstanding_amount": "25.00", "booking_ref": "B1"}],
    }
    result = compare_statements(payload([item]), payload([item]))
    assert result["counts"]["new"] == 0
    assert result["rows"][0]["change"] == "unchanged" and result["rows"][0]["still_needs_review"]
