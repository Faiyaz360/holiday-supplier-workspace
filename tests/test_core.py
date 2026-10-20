import csv

import pytest

from reconciliation.core import reconcile_files

LEDGER_FIELDS = "supplier_id,invoice_ref,booking_ref,hotel,destination,stay_date,currency,original_amount,outstanding_amount,due_date,snapshot_date".split(
    ","
)
STATEMENT_FIELDS = (
    "statement_id,supplier_id,cutoff_date,line_id,invoice_ref,currency,claimed_amount".split(",")
)


def write_csv(path, fields, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return path


def payable(ref="INV-001", **changes):
    row = dict(
        zip(
            LEDGER_FIELDS,
            [
                "SUP-PMI",
                ref,
                "BK-DEMO-001",
                "Demo Bay Hotel",
                "Mallorca",
                "2026-09-20",
                "EUR",
                "120.00",
                "80.00",
                "2026-10-17",
                "2026-10-18",
            ],
            strict=True,
        )
    )
    return row | changes


def statement(ref="INV-001", **changes):
    row = dict(
        zip(
            STATEMENT_FIELDS,
            ["ST-DEMO-PMI", "SUP-PMI", "2026-10-18", "L-001", ref, "EUR", "80.00"],
            strict=True,
        )
    )
    return row | changes


def run_case(tmp_path, statements=None, payables=None):
    return reconcile_files(
        write_csv(
            tmp_path / "statement.csv",
            STATEMENT_FIELDS,
            statements if statements is not None else [statement()],
        ),
        write_csv(
            tmp_path / "ledger.csv",
            LEDGER_FIELDS,
            payables if payables is not None else [payable()],
        ),
    )


def test_partial_settlement_matches_outstanding_not_original_price(tmp_path):
    result = run_case(tmp_path)
    assert result["rows"][0]["status"] == "matched"
    assert result["rows"][0]["overdue"] is True
    assert result["totals"]["matched"]["EUR"] == {"statement": "80.00", "ledger": "80.00"}


@pytest.mark.parametrize(
    "change,want",
    [
        ({"claimed_amount": "81.00"}, "amount_mismatch"),
        ({"currency": "GBP"}, "currency_mismatch"),
        ({"invoice_ref": "INV-UNKNOWN"}, "statement_only"),
    ],
)
def test_discrepancy_categories(tmp_path, change, want):
    result = run_case(tmp_path, [statement(**change)])
    assert result["rows"][0]["status"] == want


def test_ledger_only_and_complete_row_accounting(tmp_path):
    result = run_case(
        tmp_path, [statement()], [payable(), payable("INV-002", booking_ref="BK-DEMO-002")]
    )
    assert [r["status"] for r in result["rows"]] == ["matched", "ledger_only"]
    assert result["counts"] == {"matched": 1, "ledger_only": 1}
    assert result["input_counts"] == {"statement": 1, "ledger": 2}


@pytest.mark.parametrize("side", ["statement", "ledger"])
def test_duplicates_require_review_and_preserve_both_rows(tmp_path, side):
    statements = [statement(), statement(line_id="L-002")] if side == "statement" else [statement()]
    payables = [payable(), payable(booking_ref="BK-DEMO-002")] if side == "ledger" else [payable()]
    result = run_case(tmp_path, statements, payables)
    assert result["rows"][0]["status"] == "ambiguous"
    assert len(result["rows"][0]["statement_lines"]) == len(statements)
    assert len(result["rows"][0]["ledger_lines"]) == len(payables)
    assert "matched" not in result["totals"]


def test_reference_reuse_does_not_cross_supplier_boundary(tmp_path):
    result = run_case(
        tmp_path,
        [statement()],
        [
            payable(),
            payable(
                supplier_id="SUP-AYT",
                hotel="Other demo hotel",
                outstanding_amount="999.00",
                original_amount="999.00",
            ),
        ],
    )
    assert result["counts"] == {"matched": 1}
    assert result["input_counts"]["ledger"] == 1
    assert "999.00" not in str(result)


@pytest.mark.parametrize(
    "due_date,overdue", [("2026-10-18", False), ("2026-10-19", False), ("2026-10-17", True)]
)
def test_due_date_boundary(tmp_path, due_date, overdue):
    assert (
        run_case(tmp_path, payables=[payable(due_date=due_date)])["rows"][0]["overdue"] is overdue
    )


@pytest.mark.parametrize(
    "bad", ["", "NaN", "Infinity", "-1.00", "1.001", "1e3", "1000000000000000.00"]
)
def test_invalid_money_is_a_row_level_error(tmp_path, bad):
    with pytest.raises(ValueError, match="row 2"):
        run_case(tmp_path, [statement(claimed_amount=bad)])


@pytest.mark.parametrize(
    "change", [{"supplier_id": "OTHER"}, {"cutoff_date": "2026-10-19"}, {"line_id": "L-001"}]
)
def test_conflicting_statement_metadata_and_duplicate_line_ids_fail(tmp_path, change):
    with pytest.raises(ValueError):
        run_case(
            tmp_path,
            [
                statement(),
                statement(line_id="L-002", **{k: v for k, v in change.items() if k != "line_id"}),
            ]
            if "line_id" not in change
            else [statement(), statement()],
        )


@pytest.mark.parametrize(
    "change",
    [
        {"snapshot_date": "2026-10-19"},
        {"currency": "XYZ"},
        {"invoice_ref": ""},
        {"due_date": "18/10/2026"},
        {"original_amount": "79.00"},
    ],
)
def test_invalid_ledger_fails_before_a_clean_result(tmp_path, change):
    with pytest.raises(ValueError):
        run_case(tmp_path, payables=[payable(**change)])


def test_currency_conflict_has_separate_totals(tmp_path):
    result = run_case(tmp_path, [statement(currency="GBP")])
    assert result["totals"]["currency_mismatch"] == {
        "GBP": {"statement": "80.00", "ledger": "0.00"},
        "EUR": {"statement": "0.00", "ledger": "80.00"},
    }


def test_empty_statement_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="empty"):
        run_case(tmp_path, statements=[])


def test_oversized_statement_fails_before_matching(tmp_path):
    statement_path = tmp_path / "oversized.csv"
    statement_path.write_bytes(b"x" * 2_000_001)
    with pytest.raises(ValueError, match="2 MB"):
        reconcile_files(statement_path, tmp_path / "unused.csv")


def test_duplicate_csv_headers_are_rejected(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("supplier_id,supplier_id\nSUP-PMI,SUP-PMI\n")
    with pytest.raises(ValueError, match="duplicate CSV headers"):
        reconcile_files(path, tmp_path / "unused.csv")


def test_statement_only_invoice_has_unknown_overdue_status(tmp_path):
    result = run_case(tmp_path, [statement(invoice_ref="INV-UNRECORDED")])
    missing = next(row for row in result["rows"] if row["status"] == "statement_only")
    assert missing["overdue"] is None
