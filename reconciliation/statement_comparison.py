"""Deterministic comparison of two saved statement results."""

from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation

MAX_ROWS = 2000


def _amount(value):
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return str(value)


def _cells(row):
    cells = []
    for source, amount_key, label in (
        ("statement_lines", "claimed_amount", "statement"),
        ("ledger_lines", "outstanding_amount", "ledger"),
    ):
        for line in row.get(source) or []:
            cells.append(
                {
                    "currency": line.get("currency"),
                    "amount": _amount(line.get(amount_key, line.get("amount"))),
                    "booking_ref": line.get(
                        "booking_ref", line.get("booking", line.get("booking_reference"))
                    ),
                    "source": label,
                }
            )
    return cells


def _cell_key(cell):
    amount = cell["amount"]
    return (
        cell.get("source"),
        cell.get("currency"),
        amount if isinstance(amount, Decimal) else amount,
        cell.get("booking_ref"),
    )


def _summary(rows):
    values = []
    for row in rows:
        values.extend(_cells(row))
    return sorted(
        values,
        key=lambda item: (
            str(item.get("currency")),
            str(item.get("amount")),
            str(item.get("booking_ref")),
        ),
    )


def _public_cells(cells):
    return [
        {
            **cell,
            "amount": str(cell["amount"])
            if isinstance(cell["amount"], Decimal)
            else cell["amount"],
        }
        for cell in cells
    ]


def compare_statements(previous: dict, current: dict) -> dict:
    """Compare HostedWorkflow.read payloads without mutating either input."""
    old_result, new_result = previous["result"], current["result"]
    if old_result.get("supplier_id") != new_result.get("supplier_id"):
        raise ValueError("Statements must belong to the same supplier")
    old_rows = old_result.get("rows") or []
    new_rows = new_result.get("rows") or []
    if len(old_rows) > MAX_ROWS or len(new_rows) > MAX_ROWS:
        raise ValueError("Each saved result is limited to 2,000 invoice groups")
    old = defaultdict(list)
    new = defaultdict(list)
    for row in old_rows:
        old[row.get("invoice_ref", "")].append(row)
    for row in new_rows:
        new[row.get("invoice_ref", "")].append(row)
    result_rows = []
    for ref in sorted(set(old) | set(new)):
        before_rows, after_rows = old.get(ref, []), new.get(ref, [])
        before_cells, after_cells = _summary(before_rows), _summary(after_rows)
        before_status = before_rows[0].get("status") if before_rows else None
        after_status = after_rows[0].get("status") if after_rows else None
        before_statement = any(row.get("statement_lines") for row in before_rows)
        after_statement = any(row.get("statement_lines") for row in after_rows)
        if not before_statement and after_statement:
            change = "new"
        elif before_statement and not after_statement:
            change = "removed"
        elif (
            Counter(map(_cell_key, before_cells)) != Counter(map(_cell_key, after_cells))
            or before_status != after_status
        ):
            change = "changed"
        else:
            change = "unchanged"
        needs_review = bool(
            before_rows and after_rows and before_status != "matched" and after_status != "matched"
        )
        result_rows.append(
            {
                "invoice_ref": ref,
                "change": change,
                "before_status": before_status,
                "after_status": after_status,
                "before": _public_cells(before_cells),
                "after": _public_cells(after_cells),
                "still_needs_review": needs_review,
            }
        )
    counts = {
        key: sum(row["change"] == key for row in result_rows)
        for key in ("new", "removed", "changed", "unchanged")
    }
    counts["still_needs_review"] = sum(row["still_needs_review"] for row in result_rows)
    return {
        "previous": {
            "run_id": previous.get("meta", {}).get("run_id"),
            "statement_id": old_result.get("statement_id"),
            "cutoff_date": old_result.get("cutoff_date"),
        },
        "current": {
            "run_id": current.get("meta", {}).get("run_id"),
            "statement_id": new_result.get("statement_id"),
            "cutoff_date": new_result.get("cutoff_date"),
        },
        "counts": counts,
        "rows": result_rows,
        "same_source": all(row["change"] == "unchanged" for row in result_rows)
        and old_result.get("cutoff_date") == new_result.get("cutoff_date"),
    }
