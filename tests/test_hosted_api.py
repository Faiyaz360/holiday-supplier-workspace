from fastapi.testclient import TestClient
from test_cloud_storage import MemoryBucket
from test_hosted_workflow import model

from reconciliation.cloud_storage import CloudRunStore
from reconciliation.hosted_workflow import HostedWorkflow

READ = "read-only-example-credential"
EXECUTE = "execution-example-credential"
REVIEW = "review-only-example-credential"


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def client(*, factory=model):
    from reconciliation.hosted_api import create_hosted_app

    workflow = HostedWorkflow(CloudRunStore(MemoryBucket()), "SUP-PMI")
    app = create_hosted_app(
        workflow,
        read_token=READ,
        execution_token=EXECUTE,
        review_token=REVIEW,
        model_factory=factory,
    )
    return TestClient(app), workflow


def test_unauthenticated_calls_cannot_create_or_read_runs():
    http, workflow = client()
    assert http.post("/runs", json={}).status_code == 401
    assert not workflow.store.bucket.objects
    assert http.get("/capabilities").status_code == 401


def test_view_permission_does_not_allow_execution_or_review():
    http, _ = client()
    assert http.post("/runs", json={}, headers=auth(READ)).status_code == 403
    run = http.post("/runs", json={}, headers=auth(EXECUTE)).json()
    assert (
        http.post(
            f"/runs/{run['run_id']}/execute", json={"request_id": "request-one"}, headers=auth(READ)
        ).status_code
        == 403
    )
    assert (
        http.post(
            f"/runs/{run['run_id']}/review", json={"reviewer": "Example"}, headers=auth(READ)
        ).status_code
        == 403
    )


def test_execution_and_review_credentials_are_separate():
    http, _ = client()
    run = http.post("/runs", json={}, headers=auth(EXECUTE)).json()
    assert (
        http.post(
            f"/runs/{run['run_id']}/execute",
            json={"request_id": "request-one"},
            headers=auth(REVIEW),
        ).status_code
        == 403
    )
    assert (
        http.post(
            f"/runs/{run['run_id']}/review", json={"reviewer": "Example"}, headers=auth(EXECUTE)
        ).status_code
        == 403
    )


def test_full_authorised_http_workflow_uses_real_adk_and_keeps_financial_evidence():
    http, _ = client()
    created = http.post("/runs", json={}, headers=auth(EXECUTE))
    assert created.status_code == 200
    run_id = created.json()["run_id"]
    response = http.post(
        f"/runs/{run_id}/execute", json={"request_id": "request-one"}, headers=auth(EXECUTE)
    )
    assert response.status_code == 200
    assert response.json()["meta"]["drafting_status"] == "offline_model_verified"
    repeated = http.post(
        f"/runs/{run_id}/execute", json={"request_id": "request-one"}, headers=auth(EXECUTE)
    )
    assert repeated.json()["reused"] is True
    report = http.get(f"/runs/{run_id}/report", headers=auth(READ))
    assert report.status_code == 200
    assert "EUR 239.00" in report.text and "EUR 214.00" in report.text
    assert report.headers["cache-control"] == "no-store"
    saved = http.get(f"/runs/{run_id}", headers=auth(READ)).json()
    assert "report" not in saved
    assert "lease" not in saved and "requests" not in saved
    reviewed = http.post(
        f"/runs/{run_id}/review", json={"reviewer": "Fictional reviewer"}, headers=auth(REVIEW)
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["review"]["reviewer"] == "Fictional reviewer"
    assert reviewed.json()["transmitted"] is False


def test_client_cannot_override_supplier_model_or_server_paths():
    http, _ = client()
    assert (
        http.post("/runs", json={"supplier_id": "SUP-AYT"}, headers=auth(EXECUTE)).status_code
        == 422
    )
    run = http.post("/runs", json={}, headers=auth(EXECUTE)).json()
    assert (
        http.post(
            f"/runs/{run['run_id']}/execute",
            json={"request_id": "request-one", "model": "other-model", "output": "../private"},
            headers=auth(EXECUTE),
        ).status_code
        == 422
    )


def test_live_access_requires_the_owners_process_approval(monkeypatch):
    monkeypatch.setenv("DEMO_LIVE_APPROVED", "0")
    http, workflow = client(factory=None)
    run = http.post("/runs", json={}, headers=auth(EXECUTE)).json()
    assert (
        http.post(
            f"/runs/{run['run_id']}/execute",
            json={"request_id": "request-one"},
            headers=auth(EXECUTE),
        ).status_code
        == 403
    )
    assert workflow.read(run["run_id"])["meta"]["model_calls"] == 0


def test_capabilities_expose_permissions_but_never_configured_credentials():
    http, _ = client()
    response = http.get("/capabilities", headers=auth(REVIEW))
    assert response.status_code == 200
    assert response.json()["permissions"] == {
        "read": True,
        "create": False,
        "execute": False,
        "review": True,
    }
    assert all(token not in response.text for token in (READ, EXECUTE, REVIEW))


def test_conflicting_credentials_disable_access(monkeypatch):
    from reconciliation.hosted_api import create_hosted_app

    workflow = HostedWorkflow(CloudRunStore(MemoryBucket()), "SUP-PMI")
    app = create_hosted_app(workflow, read_token=READ, execution_token=READ, review_token=REVIEW)
    http = TestClient(app)
    assert http.get("/capabilities", headers=auth(READ)).status_code == 503
    assert http.post("/runs", json={}, headers=auth(READ)).status_code == 503


def test_storage_outage_returns_unavailable_without_private_error_text(monkeypatch, caplog):
    from google.api_core.exceptions import ServiceUnavailable

    http, workflow = client()
    run = http.post("/runs", json={}, headers=auth(EXECUTE)).json()

    def fail(*args, **kwargs):
        raise ServiceUnavailable("private-service-credential-do-not-display")

    monkeypatch.setattr(workflow.store.bucket, "get_blob", fail)
    response = http.get(f"/runs/{run['run_id']}", headers=auth(READ))
    assert response.status_code == 503
    assert "private-service-credential" not in response.text
    assert "private-service-credential" not in caplog.text


def test_missing_and_malformed_run_ids_have_safe_responses():
    http, _ = client()
    assert http.get("/runs/" + "0" * 20, headers=auth(READ)).status_code == 404
    assert http.get("/runs/not-a-run-id", headers=auth(READ)).status_code == 422


def test_active_execution_claim_blocks_a_second_execution_and_review():
    http, workflow = client()
    run_id = http.post("/runs", json={}, headers=auth(EXECUTE)).json()["run_id"]
    workflow.store.acquire(run_id, "already-running")
    response = http.get(f"/runs/{run_id}", headers=auth(READ))
    assert response.json()["execution"]["status"] == "running"
    assert (
        http.post(
            f"/runs/{run_id}/execute", json={"request_id": "second-request"}, headers=auth(EXECUTE)
        ).status_code
        == 409
    )
    assert (
        http.post(
            f"/runs/{run_id}/review", json={"reviewer": "Example"}, headers=auth(REVIEW)
        ).status_code
        == 409
    )
    assert workflow.read(run_id)["meta"]["review"] is None


def test_other_supplier_run_and_payables_are_denied():
    http, workflow = client()
    other = HostedWorkflow(workflow.store, "SUP-AYT")
    run_id = other.create()["run_id"]
    assert http.get(f"/runs/{run_id}", headers=auth(READ)).status_code == 403
    assert http.get(f"/runs/{run_id}/report", headers=auth(READ)).status_code == 403
    assert (
        http.post(
            f"/runs/{run_id}/execute", json={"request_id": "other-supplier"}, headers=auth(EXECUTE)
        ).status_code
        == 403
    )
    assert (
        http.post(
            f"/runs/{run_id}/review", json={"reviewer": "Example"}, headers=auth(REVIEW)
        ).status_code
        == 403
    )
    assert http.get("/suppliers/SUP-AYT/payables/INV-0001", headers=auth(READ)).status_code == 403
    assert other.read(run_id)["meta"]["model_calls"] == 0


def test_finance_lookup_is_authenticated_and_matches_saved_source_evidence():
    http, workflow = client()
    run_id = http.post("/runs", json={}, headers=auth(EXECUTE)).json()["run_id"]
    ledger = [
        line for group in workflow.read(run_id)["result"]["rows"] for line in group["ledger_lines"]
    ]
    row = next(item for item in ledger if item["supplier_id"] == "SUP-PMI")
    path = f"/suppliers/SUP-PMI/payables/{row['invoice_ref']}"
    assert http.get(path).status_code == 401
    response = http.get(path, headers=auth(READ))
    assert response.status_code == 200
    assert row in response.json()["payables"]


def test_hosted_statement_only_invoice_has_empty_ledger_evidence():
    from reconciliation.tools import FinanceClient, ReconciliationTools

    http, workflow = client()
    run = workflow.create()
    tools = ReconciliationTools(
        workflow.read(run["run_id"]),
        FinanceClient("http://127.0.0.1", READ, "SUP-PMI", client=http),
    )
    assert tools.lookup_payable("INV-UNRECORDED")["payables"] == []
    assert tools.get_exception("R-0100")["overdue"] is None
    assert (
        http.get("/suppliers/SUP-AYT/payables/INV-UNRECORDED", headers=auth(READ)).status_code
        == 403
    )
