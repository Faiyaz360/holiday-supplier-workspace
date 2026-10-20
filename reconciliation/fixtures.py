"""Generate reproducible hotel-payable examples. No real suppliers or travellers."""

import csv
import json
from decimal import Decimal
from pathlib import Path

from .core import LEDGER_FIELDS, STATEMENT_FIELDS

SUPPLIERS = {
    "SUP-PMI": ("Demo Balearic Hotel Group", "Mallorca", "EUR"),
    "SUP-AGP": ("Demo Andalusian Stays", "Costa del Sol", "EUR"),
    "SUP-AYT": ("Demo Turquoise Resorts", "Antalya", "GBP"),
}


def _write(path: Path, fields: list[str], rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def generate(root: Path):
    ledger = []
    expected = {}
    for supplier, (hotel, destination, currency) in SUPPLIERS.items():
        lines = []
        for i in range(1, 101):
            ref = f"INV-{i:04d}"
            original = Decimal(200 + i * 7)
            outstanding = original - Decimal(50) if i % 10 == 1 else original
            ledger.append(
                dict(
                    zip(
                        LEDGER_FIELDS,
                        [
                            supplier,
                            ref,
                            f"BK-DEMO-{supplier[-3:]}-{i:04d}",
                            hotel,
                            destination,
                            f"2026-09-{1 + i % 28:02d}",
                            currency,
                            f"{original:.2f}",
                            f"{outstanding:.2f}",
                            "2026-10-15" if i % 2 else "2026-10-25",
                            "2026-10-18",
                        ],
                        strict=True,
                    )
                )
            )
            if i == 5:
                continue
            amount = outstanding + Decimal(25) if i == 2 else outstanding
            line_currency = ("EUR" if currency == "GBP" else "GBP") if i == 3 else currency
            lines.append(
                dict(
                    zip(
                        STATEMENT_FIELDS,
                        [
                            f"ST-DEMO-{supplier[-3:]}",
                            supplier,
                            "2026-10-18",
                            f"L-{i:04d}",
                            ref,
                            line_currency,
                            f"{amount:.2f}",
                        ],
                        strict=True,
                    )
                )
            )
            if i == 4:
                lines.append(lines[-1] | {"line_id": "L-DUPLICATE"})
        lines.append(
            dict(
                zip(
                    STATEMENT_FIELDS,
                    [
                        f"ST-DEMO-{supplier[-3:]}",
                        supplier,
                        "2026-10-18",
                        "L-UNKNOWN",
                        "INV-UNRECORDED",
                        currency,
                        "345.00",
                    ],
                    strict=True,
                )
            )
        )
        _write(root / "data" / "statements" / f"{supplier}.csv", STATEMENT_FIELDS, lines)
        expected[supplier] = {
            "counts": {
                "matched": 96,
                "amount_mismatch": 1,
                "currency_mismatch": 1,
                "ambiguous": 1,
                "ledger_only": 1,
                "statement_only": 1,
            },
            "exceptions": {
                "INV-0002": "amount_mismatch",
                "INV-0003": "currency_mismatch",
                "INV-0004": "ambiguous",
                "INV-0005": "ledger_only",
                "INV-UNRECORDED": "statement_only",
            },
        }
    _write(root / "data" / "ledger.csv", LEDGER_FIELDS, ledger)
    path = root / "tests" / "fixtures" / "expected.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(expected, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    generate(Path(__file__).resolve().parents[1])
