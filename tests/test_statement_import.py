import hashlib
from pathlib import Path

import pytest

from reconciliation.runs import create_run, load_run

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "data/statements/SUP-PMI.csv"
LEDGER = ROOT / "data/ledger.csv"


def preview(raw=None):
    from reconciliation.statement_import import validate_statement

    return validate_statement(SAMPLE.read_bytes() if raw is None else raw, "SUP-PMI", LEDGER)


def test_valid_upload_preview_has_no_model_or_storage_side_effect(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    value = preview()
    assert value["row_count"] == 101
    assert value["digest"] == hashlib.sha256(SAMPLE.read_bytes()).hexdigest()
    assert value["result"]["counts"]["matched"] == 96
    assert len(value["preview_rows"]) == 10
    assert list(tmp_path.iterdir()) == []


def test_duplicate_invoice_is_a_finding_not_parse_error():
    assert preview()["result"]["counts"]["ambiguous"] == 1


def test_duplicate_line_id_is_invalid():
    raw = SAMPLE.read_bytes().replace(b"L-DUPLICATE", b"L-0004")
    with pytest.raises(ValueError, match="row 6.*duplicate"):
        preview(raw)


def test_invalid_rows_report_source_row():
    with pytest.raises(ValueError, match="row 3.*money"):
        preview(SAMPLE.read_bytes().replace(b"239.00", b"not-money", 1))


@pytest.mark.parametrize("before,after", [(b"SUP-PMI", b"SUP-AYT"), (b"2026-10-18", b"2026-10-19")])
def test_mixed_scope_rejected(before, after):
    with pytest.raises(ValueError):
        preview(SAMPLE.read_bytes().replace(before, after, 1))


def test_wrong_supplier_rejected_before_reconciliation():
    with pytest.raises(ValueError, match="supplier"):
        preview(SAMPLE.read_bytes().replace(b"SUP-PMI", b"SUP-AYT"))


def test_upload_limits_and_utf8():
    with pytest.raises(ValueError, match="1 MB"):
        preview(b"x" * 1_000_001)
    with pytest.raises(ValueError, match="UTF-8"):
        preview(b"\xff")
    lines = SAMPLE.read_bytes().splitlines()
    raw = b"\n".join(
        [lines[0]] + [lines[1].replace(b"L-0001", f"L-{i}".encode()) for i in range(1001)]
    )
    with pytest.raises(ValueError, match="1,000"):
        preview(raw)


def imported(tmp_path, raw=None):
    source = tmp_path / "input.csv"
    source.write_bytes(raw or SAMPLE.read_bytes())
    meta = create_run(
        source, LEDGER, tmp_path / "runs", preserve_sources=True, source_kind="uploaded"
    )
    return meta, tmp_path / "runs"


def test_upload_is_not_labelled_verified_fixture(tmp_path):
    meta, output = imported(tmp_path)
    saved = load_run(meta["run_id"], output)
    assert saved["result"]["demo"] is False
    assert saved["meta"]["source_kind"] == "uploaded"
    assert "Fictional travel records" not in (output / meta["run_id"] / "report.html").read_text(
        encoding="utf-8"
    )


def test_saved_source_bytes_preserve_crlf_and_bom(tmp_path):
    from reconciliation.source_evidence import resolve_sources

    raw = (
        b"\xef\xbb\xbf"
        + SAMPLE.read_bytes()
        .decode("utf-8-sig")
        .replace("\r\n", "\n")
        .replace("\n", "\r\n")
        .encode()
    )
    meta, output = imported(tmp_path, raw)
    left, right = resolve_sources(meta["run_id"], output, SAMPLE, LEDGER)
    assert left.read_bytes() == raw
    assert right.read_bytes() == LEDGER.read_bytes()


def test_source_manifest_tampering_refused(tmp_path):
    meta, output = imported(tmp_path)
    (output / meta["run_id"] / "source-statement.csv").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="integrity|source"):
        load_run(meta["run_id"], output)


def test_legacy_run_still_loads(tmp_path):
    from reconciliation.source_evidence import resolve_sources

    meta = create_run(SAMPLE, LEDGER, tmp_path)
    assert "source_kind" not in meta
    assert resolve_sources(meta["run_id"], tmp_path, SAMPLE, LEDGER) == (SAMPLE, LEDGER)
    assert load_run(meta["run_id"], tmp_path)["result"]["demo"] is True


def test_upload_acceptance_uses_provenance(tmp_path):
    from reconciliation.acceptance import assess_saved_run
    from reconciliation.source_evidence import resolve_sources

    meta, output = imported(tmp_path)
    left, right = resolve_sources(meta["run_id"], output, SAMPLE, LEDGER)
    assessment = assess_saved_run(meta["run_id"], output, left, right)
    assert assessment["checks"]["source_files_unchanged"] is True
    assert assessment["checks"]["current_reconciliation_matches"] is True
