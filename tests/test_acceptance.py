import asyncio
import hashlib

import pytest
from test_agent import ScriptedModel
from test_runs import make_run

from reconciliation.agent_runner import execute_run
from reconciliation.report import INTRODUCTIONS
from reconciliation.runs import load_run, run_folder, save_meta


def audit(tmp_path, run):
    from reconciliation.acceptance import assess_saved_run

    return assess_saved_run(
        run["run_id"],
        tmp_path / "outputs",
        tmp_path / "data/statements/SUP-PMI.csv",
        tmp_path / "data/ledger.csv",
    )


def completed_run(tmp_path):
    run = make_run(tmp_path)
    return asyncio.run(
        execute_run(
            run["run_id"],
            tmp_path / "outputs",
            model=ScriptedModel(
                responses=[
                    {"name": "get_reconciliation", "args": {}},
                    {"name": "lookup_payable", "args": {"invoice_ref": "INV-0001"}},
                    {"name": "lookup_payable", "args": {"invoice_ref": "INV-0002"}},
                    {
                        "name": "propose_query",
                        "args": {
                            "exception_ids": ["R-0101", "R-0004", "R-0002", "R-0003", "R-0100"],
                            "introduction": INTRODUCTIONS[0],
                        },
                    },
                ]
            ),
        )
    )


def test_factual_audit_accepts_additional_permitted_reads_and_changes_no_evidence(tmp_path):
    run = completed_run(tmp_path)
    folder = run_folder(run["run_id"], tmp_path / "outputs")
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir()}
    result = audit(tmp_path, run)
    assert result["contract_status"] == "passed"
    assert result["evidence_mode"] == "offline_integration"
    assert result["checks"]["factual_query"] is True
    assert result["live_generation"] is False
    assert before == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir()}


def test_template_is_not_reported_as_agent_validation(tmp_path):
    result = audit(tmp_path, make_run(tmp_path))
    assert result["contract_status"] == "incomplete"
    assert result["evidence_mode"] == "template_only"
    assert result["checks"]["factual_query"] is None


def test_incorrect_saved_due_status_fails_even_when_checksums_are_valid(tmp_path):
    run = completed_run(tmp_path)
    data = load_run(run["run_id"], tmp_path / "outputs")
    next(r for r in data["result"]["rows"] if r["invoice_ref"] == "INV-UNRECORDED")["overdue"] = (
        False
    )
    folder = run_folder(run["run_id"], tmp_path / "outputs")
    import json

    facts = (json.dumps(data["result"], indent=2, sort_keys=True) + "\n").encode()
    (folder / "result.json").write_bytes(facts)
    run["result_sha256"] = hashlib.sha256(facts).hexdigest()
    save_meta(folder, data["result"], run)
    result = audit(tmp_path, run)
    assert result["contract_status"] == "failed"
    assert result["checks"]["current_reconciliation_matches"] is False


def test_correct_numbers_in_an_unapproved_sentence_do_not_pass(tmp_path):
    run = completed_run(tmp_path)
    saved = load_run(run["run_id"], tmp_path / "outputs")
    run["query_text"] += "\nEUR 239.00 is approved for payment."
    save_meta(run_folder(run["run_id"], tmp_path / "outputs"), saved["result"], run)
    result = audit(tmp_path, run)
    assert result["contract_status"] == "failed"
    assert result["checks"]["factual_query"] is False


def test_forbidden_recorded_action_does_not_pass(tmp_path):
    run = completed_run(tmp_path)
    saved = load_run(run["run_id"], tmp_path / "outputs")
    run["events"].append({"event": "tool_call", "tool": "send_email"})
    save_meta(run_folder(run["run_id"], tmp_path / "outputs"), saved["result"], run)
    result = audit(tmp_path, run)
    assert result["contract_status"] == "failed"
    assert result["checks"]["only_allowed_tools"] is False


def test_changed_input_requires_fresh_reconciliation(tmp_path):
    run = make_run(tmp_path)
    ledger = tmp_path / "data/ledger.csv"
    ledger.write_bytes(ledger.read_bytes().replace(b"2026-10-25", b"2026-10-24"))
    result = audit(tmp_path, run)
    assert result["contract_status"] == "failed"
    assert result["checks"]["source_files_unchanged"] is False


@pytest.mark.parametrize(
    "field,value,check",
    [("transmitted", True, "no_transmission"), ("model_calls", 11, "model_budget")],
)
def test_claimed_completion_cannot_hide_an_unsafe_saved_state(tmp_path, field, value, check):
    run = completed_run(tmp_path)
    saved = load_run(run["run_id"], tmp_path / "outputs")
    run[field] = value
    save_meta(run_folder(run["run_id"], tmp_path / "outputs"), saved["result"], run)
    result = audit(tmp_path, run)
    assert result["contract_status"] == "failed"
    assert result["checks"][check] is False


def test_malformed_tool_name_is_rejected_without_crashing_the_audit(tmp_path):
    run = completed_run(tmp_path)
    saved = load_run(run["run_id"], tmp_path / "outputs")
    run["events"].insert(1, {"event": "tool_call", "tool": {"unexpected": "value"}})
    save_meta(run_folder(run["run_id"], tmp_path / "outputs"), saved["result"], run)
    result = audit(tmp_path, run)
    assert result["contract_status"] == "failed"
    assert result["checks"]["only_allowed_tools"] is False
