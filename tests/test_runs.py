import json
from pathlib import Path

import pytest
from test_core import payable, run_case

from reconciliation.core import reconcile_files
from reconciliation.fixtures import SUPPLIERS, generate
from reconciliation.runs import create_run, load_run, review_run

ROOT = Path(__file__).resolve().parents[1]


def test_generated_travel_fixtures_match_the_independent_answer_sheet():
    expected = json.loads((ROOT / "tests/fixtures/expected.json").read_text())
    for supplier in SUPPLIERS:
        result = reconcile_files(ROOT / f"data/statements/{supplier}.csv", ROOT / "data/ledger.csv")
        assert result["counts"] == expected[supplier]["counts"]
        assert {
            r["invoice_ref"]: r["status"] for r in result["rows"] if r["status"] != "matched"
        } == expected[supplier]["exceptions"]
        assert result["input_counts"] == {"statement": 101, "ledger": 100}
        assert sum(len(r["statement_lines"]) for r in result["rows"]) == 101
        assert sum(len(r["ledger_lines"]) for r in result["rows"]) == 100


def make_run(tmp_path):
    generate(tmp_path)
    return create_run(
        tmp_path / "data/statements/SUP-PMI.csv", tmp_path / "data/ledger.csv", tmp_path / "outputs"
    )


def test_saved_run_is_repeatable_and_has_an_unreviewed_factual_report(tmp_path):
    first = make_run(tmp_path)
    second = create_run(
        tmp_path / "data/statements/SUP-PMI.csv", tmp_path / "data/ledger.csv", tmp_path / "outputs"
    )
    assert first["run_id"] == second["run_id"]
    saved = load_run(first["run_id"], tmp_path / "outputs")
    assert saved["meta"]["review"] is None
    assert saved["meta"]["drafting_status"] == "offline_template"
    report = (tmp_path / "outputs" / first["run_id"] / "report.html").read_text(encoding="utf-8")
    assert "Mallorca" in report and "Fictional travel records" in report
    assert "INV-0002" in report and "EUR 239.00" in report
    assert saved["meta"]["model_calls"] == 0


def test_html_escapes_supplier_evidence(tmp_path):
    from reconciliation.report import render_report

    result = run_case(tmp_path, payables=[payable(hotel='<img src=x onerror="alert(1)">')])
    html = render_report(
        result, {"run_id": "abc", "drafting_status": "offline_template", "review": None}
    )
    assert "<img src=x" not in html
    assert "&lt;img src=x" in html


def test_review_is_idempotent_and_records_no_transmission(tmp_path):
    run = make_run(tmp_path)
    first = review_run(run["run_id"], "Demo reviewer", tmp_path / "outputs")
    second = review_run(run["run_id"], "Demo reviewer", tmp_path / "outputs")
    assert first["review"] == second["review"]
    assert first["review"]["action"] == "reviewed_locally"
    assert first["transmitted"] is False


@pytest.mark.parametrize("target", ["result.json", "report.html"])
def test_tampering_prevents_review(tmp_path, target):
    run = make_run(tmp_path)
    path = tmp_path / "outputs" / run["run_id"] / target
    path.write_text(path.read_text(encoding="utf-8") + "tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="integrity"):
        review_run(run["run_id"], "Demo reviewer", tmp_path / "outputs")


@pytest.mark.parametrize("run_id", ["../secret", "../../.env", "abc/def", "", "C:\\secrets"])
def test_run_lookup_rejects_unsafe_identifiers(tmp_path, run_id):
    with pytest.raises(ValueError):
        load_run(run_id, tmp_path)
