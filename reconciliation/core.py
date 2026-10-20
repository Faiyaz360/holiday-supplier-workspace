"""Exact reconciliation of fictional hotel supplier open-item snapshots."""

import csv
import re
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

LEDGER_FIELDS = "supplier_id invoice_ref booking_ref hotel destination stay_date currency original_amount outstanding_amount due_date snapshot_date".split()
STATEMENT_FIELDS = (
    "statement_id supplier_id cutoff_date line_id invoice_ref currency claimed_amount".split()
)
CURRENCIES = {"GBP", "EUR", "USD"}
MAX_ROWS = 10000


def _read(path: Path, fields: list[str]) -> list[dict]:
    if path.stat().st_size > 2_000_000:
        raise ValueError("CSV exceeds the 2 MB demo limit")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise ValueError("Missing or duplicate CSV headers")
        if set(reader.fieldnames) != set(fields):
            raise ValueError("CSV headers do not match the documented data contract")
        rows = []
        for number, raw in enumerate(reader, 2):
            if len(rows) >= MAX_ROWS:
                raise ValueError("CSV exceeds the 10000-row demo limit")
            if None in raw or any(value is None for value in raw.values()):
                raise ValueError(f"row {number}: inconsistent column count")
            row = {key: value.strip() for key, value in raw.items()}
            if any(
                not value or len(value) > 200 or any(ord(c) < 32 for c in value)
                for value in row.values()
            ):
                raise ValueError(f"row {number}: blank, oversized or invalid field")
            if row["currency"] not in CURRENCIES:
                raise ValueError(f"row {number}: unsupported currency")
            if not re.fullmatch(r"[A-Z0-9-]{1,40}", row["supplier_id"]):
                raise ValueError(f"row {number}: invalid supplier ID")
            row["source_row"] = number
            rows.append(row)
    if not rows:
        raise ValueError("CSV is empty; an open-item snapshot must have rows")
    return rows


def _money(value: str, row: int) -> Decimal:
    if not re.fullmatch(r"[0-9]{1,12}(?:\.[0-9]{1,2})?", value):
        raise ValueError(
            f"row {row}: invalid money; use non-negative decimal with at most two places"
        )
    return Decimal(value).quantize(Decimal("0.01"))


def _date(value: str, row: int) -> date:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError(f"row {row}: date must use YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"row {row}: invalid date") from None


def load_statement(path: Path) -> dict:
    rows = _read(path, STATEMENT_FIELDS)
    metadata = ("statement_id", "supplier_id", "cutoff_date")
    first = rows[0]
    seen = set()
    for row in rows:
        number = row["source_row"]
        if any(row[key] != first[key] for key in metadata):
            raise ValueError(f"row {number}: conflicting statement metadata")
        if row["line_id"] in seen:
            raise ValueError(f"row {number}: duplicate statement line ID")
        seen.add(row["line_id"])
        row["claimed_amount"] = str(_money(row["claimed_amount"], number))
    cutoff = _date(first["cutoff_date"], 2)
    return {
        "statement_id": first["statement_id"],
        "supplier_id": first["supplier_id"],
        "cutoff_date": cutoff.isoformat(),
        "lines": rows,
    }


def load_ledger(path: Path, cutoff: date) -> list[dict]:
    rows = _read(path, LEDGER_FIELDS)
    for row in rows:
        number = row["source_row"]
        if _date(row["snapshot_date"], number) != cutoff:
            raise ValueError(f"row {number}: ledger snapshot differs from statement cutoff")
        _date(row["stay_date"], number)
        _date(row["due_date"], number)
        original = _money(row["original_amount"], number)
        outstanding = _money(row["outstanding_amount"], number)
        if outstanding > original:
            raise ValueError(f"row {number}: outstanding amount exceeds original invoice")
        row["original_amount"] = str(original)
        row["outstanding_amount"] = str(outstanding)
    return rows


def reconcile(statement: dict, ledger: list[dict], *, demo: bool = True) -> dict:
    supplier = statement["supplier_id"]
    scoped = [
        row
        for row in ledger
        if row["supplier_id"] == supplier and Decimal(row["outstanding_amount"]) > 0
    ]
    left, right = defaultdict(list), defaultdict(list)
    for row in statement["lines"]:
        left[row["invoice_ref"]].append(row)
    for row in scoped:
        right[row["invoice_ref"]].append(row)
    keys = list(dict.fromkeys([*left, *right]))
    totals = {}
    results = []
    for index, ref in enumerate(keys, 1):
        statements, payables = left[ref], right[ref]
        if len(statements) > 1 or len(payables) > 1:
            status = "ambiguous"
        elif not payables:
            status = "statement_only"
        elif not statements:
            status = "ledger_only"
        elif statements[0]["currency"] != payables[0]["currency"]:
            status = "currency_mismatch"
        elif statements[0]["claimed_amount"] != payables[0]["outstanding_amount"]:
            status = "amount_mismatch"
        else:
            status = "matched"
        overdue = (
            any(
                row["due_date"] < statement["cutoff_date"]
                and Decimal(row["outstanding_amount"]) > 0
                for row in payables
            )
            if payables
            else None
        )
        results.append(
            {
                "id": f"R-{index:04d}",
                "invoice_ref": ref,
                "status": status,
                "overdue": overdue,
                "statement_lines": statements,
                "ledger_lines": payables,
            }
        )
        for side, lines, field in [
            ("statement", statements, "claimed_amount"),
            ("ledger", payables, "outstanding_amount"),
        ]:
            for row in lines:
                amounts = totals.setdefault(status, {}).setdefault(
                    row["currency"], {"statement": Decimal(0), "ledger": Decimal(0)}
                )
                amounts[side] += Decimal(row[field])
    totals = {
        status: {
            currency: {side: f"{amount:.2f}" for side, amount in amounts.items()}
            for currency, amounts in currencies.items()
        }
        for status, currencies in totals.items()
    }
    return {
        "demo": demo,
        "supplier_id": supplier,
        "statement_id": statement["statement_id"],
        "cutoff_date": statement["cutoff_date"],
        "assumption": "Complete supplier open-item statement and ledger snapshot at the same cutoff; no foreign exchange conversion.",
        "input_counts": {"statement": len(statement["lines"]), "ledger": len(scoped)},
        "counts": dict(Counter(row["status"] for row in results)),
        "totals": totals,
        "rows": results,
    }


def reconcile_files(statement_path: Path, ledger_path: Path, *, demo: bool = True) -> dict:
    statement = load_statement(Path(statement_path))
    ledger = load_ledger(Path(ledger_path), date.fromisoformat(statement["cutoff_date"]))
    return reconcile(statement, ledger, demo=demo)
