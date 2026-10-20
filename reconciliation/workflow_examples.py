"""Read-only fictional examples built using the same reconciliation and comparison code."""

import csv
import io
from pathlib import Path
from tempfile import TemporaryDirectory

from .runs import create_run, load_run
from .statement_comparison import compare_statements


def comparison_example(data_root: Path, supplier_id: str):
    source = data_root / "statements" / f"{supplier_id}.csv"
    rows = list(csv.DictReader(io.StringIO(source.read_text(encoding="utf-8"))))
    with TemporaryDirectory(prefix="comparison-example-") as temporary:
        root = Path(temporary)
        results = []
        for index, label in enumerate(["ST-PMI-ORIGINAL", "ST-PMI-REVISED"]):
            lines = [dict(row, statement_id=label) for row in rows]
            if index:
                lines = [row for row in lines if row["invoice_ref"] != "INV-0003"]
                for row in lines:
                    if row["invoice_ref"] == "INV-0002":
                        row["claimed_amount"] = "214.00"
                lines.append(
                    dict(
                        lines[0],
                        line_id="NEW-LINE",
                        invoice_ref="INV-NEW-HOTEL",
                        claimed_amount="80.00",
                    )
                )
            path = root / f"statement-{index}.csv"
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(lines)
            output = root / f"result-{index}"
            meta = create_run(
                path,
                data_root / "ledger.csv",
                output,
                preserve_sources=True,
                source_kind="uploaded",
            )
            results.append(load_run(meta["run_id"], output))
        return compare_statements(*results) | {
            "example": True,
            "example_note": "Fictional original and revised Mallorca statements; no saved workspace records were changed.",
        }
