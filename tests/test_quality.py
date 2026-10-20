import json
from pathlib import Path

from test_hosted_workflow import model, service

from reconciliation.quality import evaluation_snapshot, recorded_timeline

ROOT = Path(__file__).resolve().parents[1]


def test_historical_snapshot_preserves_failed_scores_and_source_provenance():
    snapshot = evaluation_snapshot()
    assert snapshot["available"] is True
    assert len(snapshot["cases"]) == 12
    assert snapshot["metrics"]["tool_trajectory_avg_score"]["passed"] == 12
    assert snapshot["metrics"]["response_match_score"]["passed"] == 1
    assert snapshot["metrics"]["response_match_score"]["total"] == 12
    assert len(snapshot["sources"]) == 12 and len(snapshot["config_sha256"]) == 64
    assert any(c["id"] == "access_boundary" for c in snapshot["cases"])


def test_missing_or_corrupt_snapshot_is_unavailable(tmp_path):
    assert evaluation_snapshot(tmp_path / "missing.json")["available"] is False
    p = tmp_path / "bad.json"
    p.write_text('{"schema_version":1}', encoding="utf-8")
    assert evaluation_snapshot(p)["available"] is False


def test_template_checks_do_not_claim_completed_model_validation():
    app = service()
    ref = app.create()["run_id"]
    before = app.read(ref)
    quality = app.quality(ref)
    assert quality["current"]["checks"]["source_files_unchanged"] == "passed"
    assert quality["current"]["checks"]["factual_query"] == "not_run"
    assert quality["current"]["model_calls"] == 0 and quality["current"]["reported_tokens"] is None
    assert app.read(ref) == before
    app.execute(ref, "quality-draft-01", model=model())
    after = app.quality(ref)
    assert after["current"]["checks"]["factual_query"] == "passed"
    assert after["current"]["execution_kind"] == "offline_model_verified"


def test_timeline_uses_only_recorded_fields_and_never_leaks_raw_details():
    data = {
        "result": {"rows": [{"id": "R-0002", "invoice_ref": "INV-0002"}]},
        "meta": {
            "events": [
                {
                    "event": "tool_call",
                    "tool": "get_exception",
                    "evidence_id": "R-0002",
                    "secret": "never-show",
                },
                {"event": "unrecognised", "private": "never-show"},
                {"event": "drafting_finished"},
            ],
            "drafting_status": "provider_error",
            "review": None,
        },
    }
    timeline = recorded_timeline(data)
    assert timeline[0]["evidence_ids"] == ["R-0002"]
    assert all("timestamp" not in item for item in timeline)
    text = json.dumps(timeline)
    assert "never-show" not in text and "success" not in text
    assert timeline[-1]["state"] == "incomplete"


def test_new_adk_events_include_only_trusted_invoice_references():
    from test_agent import ScriptedModel

    app = service()
    ref = app.create()["run_id"]
    model_instance = ScriptedModel(
        responses=[{"name": "get_exception", "args": {"exception_id": "R-0002"}}]
    )
    app.execute(ref, "timeline-evidence-01", model=model_instance)
    events = app.read(ref)["meta"]["events"]
    event = next(item for item in events if item.get("tool") == "get_exception")
    assert event["evidence_id"] == "R-0002"
    assert any(item["evidence_ids"] == ["R-0002"] for item in app.quality(ref)["timeline"])


def test_incomplete_run_cannot_hide_a_failed_quality_check(monkeypatch):
    import reconciliation.hosted_workflow as module

    app = service()
    ref = app.create()["run_id"]
    monkeypatch.setattr(
        module, "assess_saved_run", lambda *args: {"checks": {"model_budget": False}}
    )
    assert app.quality(ref)["current"]["checks"]["model_budget"] == "failed"
