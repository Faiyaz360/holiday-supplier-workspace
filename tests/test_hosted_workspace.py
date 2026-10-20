import base64
import hashlib
import re

from test_hosted_api import EXECUTE, READ, REVIEW, auth, client


def test_workspace_is_available_without_exposing_saved_records_or_credentials():
    http, workflow = client()
    response = http.get("/")
    assert response.status_code == 200
    assert "Supplier operations · loveholidays" in response.text
    assert 'type="password"' in response.text
    assert 'title="Saved invoice evidence"' in response.text
    assert 'sandbox="allow-scripts allow-downloads allow-modals"' in response.text
    assert ">Sample data</span>" in response.text
    assert 'id="assistant-title"' in response.text
    assert 'role="tablist"' in response.text
    assert "Independent demo" not in response.text
    assert not workflow.store.bucket.objects
    assert all(token not in response.text for token in (READ, EXECUTE, REVIEW))
    assert response.headers["cache-control"] == "no-store"


def test_connected_workspace_limits_scripts_and_connections_to_the_service():
    http, _ = client()
    response = http.get("/")
    script = re.search(r"<script>(.*?)</script>", response.text, re.S)[1]
    digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    assert f"script-src 'sha256-{digest}'" in response.headers["content-security-policy"]
    assert "connect-src 'self'" in response.headers["content-security-policy"]
    assert "script-src 'unsafe-inline'" not in response.headers["content-security-policy"]
    assert "sessionStorage" not in script
    # The guided tour stores only its version/completion preference.
    assert "supplier-workspace-tour-v3" in script
    assert "https://" not in response.text


def test_workspace_report_stays_the_same_as_the_saved_evidence():
    http, workflow = client()
    run_id = http.post("/runs", json={}, headers=auth(EXECUTE)).json()["run_id"]
    response = http.get(f"/runs/{run_id}/report", headers=auth(READ))
    assert response.text == workflow.read(run_id)["report"]


def test_outer_policy_permits_the_exact_embedded_report_script():
    from pathlib import Path

    http, _ = client()
    script = (Path(__file__).parents[1] / "reconciliation/assets/workspace.js").read_text(
        encoding="utf-8"
    )
    digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    assert f"'sha256-{digest}'" in http.get("/").headers["content-security-policy"]


def test_connected_view_returns_the_same_query_as_the_saved_report():
    from html import unescape

    http, workflow = client()
    run_id = http.post("/runs", json={}, headers=auth(EXECUTE)).json()["run_id"]
    data = http.get(f"/runs/{run_id}", headers=auth(READ)).json()
    expected = unescape(
        re.search(r'<pre id="draft-text">(.*?)</pre>', workflow.read(run_id)["report"], re.S)[1]
    )
    assert data["draft_text"] == expected
    assert "EUR 239.00" in data["draft_text"] and "EUR 214.00" in data["draft_text"]
    assert "INV-UNRECORDED" in data["draft_text"]


def test_statement_upload_has_accessible_preview_and_explicit_confirmation():
    http, _ = client()
    html = http.get("/").text
    assert 'id="statement-import"' in html
    assert 'for="statement-file"' in html and 'type="file"' in html
    assert 'id="import-feedback"' in html and 'role="status"' in html
    assert "Confirm and reconcile" in html
    assert "Download sample" in html and "Use sample statement" in html
    assert 'data-tour="upload"' in html
    assert "1 MB" in html and "1,000" in html
    assert "CSV columns" in html


def test_question_panel_has_explicit_ask_and_evidence_controls():
    http, _ = client()
    html = http.get("/").text
    assert 'id="question-form"' in html
    assert 'id="question-input"' in html and 'maxlength="1000"' in html
    assert 'id="conversation-list"' in html
    assert "Ask assistant" in html
    assert "Questions are visible to everyone with workspace access." in html


def test_help_offers_isolated_recovery_without_exposing_internal_controls():
    http, _ = client()
    html = http.get("/").text
    assert 'id="help-link"' in html and 'aria-controls="help-panel"' in html
    assert 'id="help-panel"' in html and 'aria-labelledby="help-title"' in html
    assert "Try a service outage" in html and "Try misleading text" in html
    assert "Your saved statements stay unchanged." in html


def test_supplier_email_surface_is_wide_safe_and_keeps_source_review_controls():
    http, _ = client()
    html = http.get("/").text
    assert "AI model comparison" in html and "Decision lab" not in html
    assert "Original saved query and source rows" in html
    assert 'id="query-source-text"' in html and 'id="query-view-evidence"' in html
    assert "Copy email draft" in html and "Confirm your review" in html
    assert "border-top:4px" not in html
    assert '.review-layout:has(#tab-query[aria-selected="true"])' in html
    assert 'el("query-text").textContent = saved.supplier_email || saved.draft_text' in html
    assert "new Blob([saved.supplier_email || saved.draft_text]" in html
    assert re.search(
        r"navigator\.clipboard\.writeText\(\s*data\.supplier_email\s*\|\|\s*data\.draft_text,?\s*\)",
        html,
    )
