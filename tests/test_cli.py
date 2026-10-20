import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def command(*args):
    return subprocess.run(
        [sys.executable, "-m", "reconciliation", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=os.environ | {"PYTHONUTF8": "1"},
    )


def test_cli_produces_a_real_report_and_safe_repeatable_review(tmp_path):
    first = command("demo", "--supplier", "SUP-AYT", "--output", str(tmp_path))
    assert first.returncode == 0, first.stderr
    meta = json.loads(first.stdout)
    report = tmp_path / meta["run_id"] / "report.html"
    assert report.exists()
    assert "Antalya" in report.read_text(encoding="utf-8")
    assert meta["drafting_status"] == "offline_template"
    second = command("review", meta["run_id"], "--reviewer", "Person", "--output", str(tmp_path))
    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout)["review"]["action"] == "reviewed_locally"


def test_failure_demonstration_preserves_report_without_model_usage(tmp_path):
    result = command("demo", "--simulate-outage", "--output", str(tmp_path))
    assert result.returncode == 0, result.stderr
    meta = json.loads(result.stdout)
    assert meta["drafting_status"] == "simulated_provider_outage"
    assert meta["model_calls"] == 0
    assert (tmp_path / meta["run_id"] / "report.html").exists()


def test_bad_supplier_is_a_clear_error():
    result = command("demo", "--supplier", "SUP-OTHER")
    assert result.returncode != 0
    assert "invalid choice" in result.stderr


def test_cli_audit_uses_the_uploaded_run_sources(tmp_path):
    from reconciliation.runs import create_run

    source = tmp_path / "uploaded.csv"
    source.write_bytes(
        (ROOT / "data/statements/SUP-PMI.csv").read_bytes().replace(b"239.00", b"249.00", 1)
    )
    output = tmp_path / "runs"
    run = create_run(
        source, ROOT / "data/ledger.csv", output, preserve_sources=True, source_kind="uploaded"
    )
    result = command("audit", run["run_id"], "--output", str(output))
    assessment = json.loads(result.stdout)
    assert assessment["checks"]["source_files_unchanged"] is True
    assert assessment["checks"]["current_reconciliation_matches"] is True
