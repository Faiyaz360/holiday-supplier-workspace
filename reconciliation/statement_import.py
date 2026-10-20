"""Validate user-provided supplier CSVs without saving a run or using a model."""

import csv
import hashlib
import io
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from .core import load_ledger, load_statement, reconcile

MAX_UPLOAD_BYTES = 1_000_000
MAX_UPLOAD_ROWS = 1000


def validate_statement(raw: bytes, supplier_id: str, ledger_path: Path) -> dict:
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError("CSV exceeds the 1 MB upload limit")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeError:
        raise ValueError("Save the CSV using UTF-8 encoding") from None
    try:
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        next(reader, None)
        count = 0
        for row in reader:
            if row:
                count += 1
            if count > MAX_UPLOAD_ROWS:
                raise ValueError("CSV exceeds the 1,000-row upload limit")
    except csv.Error:
        raise ValueError("CSV contains malformed or oversized fields") from None
    with TemporaryDirectory(prefix="statement-preview-") as temporary:
        path = Path(temporary) / "statement.csv"
        path.write_bytes(raw)
        try:
            statement = load_statement(path)
            if statement["supplier_id"] != supplier_id:
                raise ValueError("This workspace supports the configured Mallorca supplier only")
            ledger = load_ledger(ledger_path, date.fromisoformat(statement["cutoff_date"]))
        except csv.Error:
            raise ValueError("CSV contains malformed fields") from None
    result = reconcile(statement, ledger, demo=False)
    return {
        "digest": hashlib.sha256(raw).hexdigest(),
        "supplier_id": statement["supplier_id"],
        "statement_id": statement["statement_id"],
        "cutoff_date": statement["cutoff_date"],
        "row_count": len(statement["lines"]),
        "preview_rows": statement["lines"][:10],
        "result": result,
    }
