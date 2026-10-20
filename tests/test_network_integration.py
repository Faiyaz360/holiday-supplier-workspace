"""Actual loopback HTTP integration; no third-party network or live model."""

import asyncio
import secrets
import socket
import threading
import time

import httpx
import uvicorn
from test_agent import ScriptedModel
from test_runs import make_run

from reconciliation.agent_runner import execute_run
from reconciliation.api import create_app
from reconciliation.report import INTRODUCTIONS


def test_adk_tool_retrieves_hotel_invoice_over_real_http(tmp_path, monkeypatch):
    token = secrets.token_urlsafe(24)
    monkeypatch.setenv("DEMO_API_TOKEN", token)
    monkeypatch.setenv("DEMO_SUPPLIER_ID", "SUP-PMI")
    run = make_run(tmp_path)
    app = create_app(tmp_path / "outputs", tmp_path / "data")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(
                app, host="127.0.0.1", port=port, log_level="critical", access_log=False, ws="none"
            )
        )
        worker = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        worker.start()
        try:
            for _ in range(100):
                if server.started:
                    break
                time.sleep(0.02)
            assert server.started, "Local finance service did not start"
            url = f"http://127.0.0.1:{port}"
            assert httpx.get(url + "/health", timeout=2, trust_env=False).status_code == 200
            monkeypatch.setenv("DEMO_API_BASE_URL", url)
            model = ScriptedModel(
                responses=[
                    {"name": "lookup_payable", "args": {"invoice_ref": "INV-0002"}},
                    {
                        "name": "propose_query",
                        "args": {
                            "exception_ids": ["R-0002", "R-0003", "R-0004", "R-0100", "R-0101"],
                            "introduction": INTRODUCTIONS[0],
                        },
                    },
                ]
            )
            meta = asyncio.run(execute_run(run["run_id"], tmp_path / "outputs", model=model))
            assert meta["drafting_status"] == "offline_model_verified"
            assert meta["evidence_source"] == "simulated_finance_api"
            assert "EUR 214.00" in meta["query_text"]
            assert meta["model_calls"] == 3
            assert {e["tool"] for e in meta["events"] if e["event"] == "tool_call"} == {
                "lookup_payable",
                "propose_query",
            }
        finally:
            server.should_exit = True
            worker.join(timeout=5)


def test_hosted_adk_investigation_reads_its_finance_endpoint_and_persists_review(monkeypatch):
    from test_cloud_storage import MemoryBucket
    from test_hosted_api import EXECUTE, READ, REVIEW, auth

    from reconciliation.cloud_storage import CloudRunStore
    from reconciliation.hosted_api import create_hosted_app
    from reconciliation.hosted_workflow import HostedWorkflow

    monkeypatch.setenv("DEMO_API_TOKEN", READ)
    workflow = HostedWorkflow(CloudRunStore(MemoryBucket()), "SUP-PMI")
    models = []

    def factory():
        model = ScriptedModel(
            responses=[
                {"name": "lookup_payable", "args": {"invoice_ref": "INV-0002"}},
                {
                    "name": "propose_query",
                    "args": {
                        "exception_ids": ["R-0002", "R-0003", "R-0004", "R-0100", "R-0101"],
                        "introduction": INTRODUCTIONS[0],
                    },
                },
            ]
        )
        models.append(model)
        return model

    app = create_hosted_app(
        workflow,
        read_token=READ,
        execution_token=EXECUTE,
        review_token=REVIEW,
        model_factory=factory,
    )
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        server = uvicorn.Server(
            uvicorn.Config(app, log_level="critical", access_log=False, ws="none")
        )
        worker = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        worker.start()
        try:
            for _ in range(100):
                if server.started:
                    break
                time.sleep(0.02)
            assert server.started
            url = f"http://127.0.0.1:{listener.getsockname()[1]}"
            monkeypatch.setenv("DEMO_API_BASE_URL", url)
            with httpx.Client(base_url=url, timeout=20, trust_env=False) as http:
                assert http.get("/").status_code == 200
                assert http.get("/suppliers/SUP-PMI/payables/INV-0002").status_code == 401
                run_id = http.post("/runs", json={}, headers=auth(EXECUTE)).json()["run_id"]
                completed = http.post(
                    f"/runs/{run_id}/execute",
                    headers=auth(EXECUTE),
                    json={"request_id": "http-integration-request"},
                )
                assert completed.status_code == 200
                meta = completed.json()["meta"]
                assert meta["drafting_status"] == "offline_model_verified"
                assert meta["evidence_source"] == "simulated_finance_api"
                assert [e["tool"] for e in meta["events"] if e["event"] == "tool_call"] == [
                    "lookup_payable",
                    "propose_query",
                ]
                assert "EUR 239.00" in meta["query_text"] and "EUR 214.00" in meta["query_text"]
                repeated = http.post(
                    f"/runs/{run_id}/execute",
                    headers=auth(EXECUTE),
                    json={"request_id": "http-integration-request"},
                )
                assert repeated.json()["reused"] is True
                assert [model.cursor for model in models] == [3, 0]
                reviewed = http.post(
                    f"/runs/{run_id}/review",
                    headers=auth(REVIEW),
                    json={"reviewer": "Fictional integration reviewer"},
                )
                assert reviewed.status_code == 200
                saved = http.get(f"/runs/{run_id}", headers=auth(READ)).json()
                assert saved["meta"]["review"]["reviewer"] == "Fictional integration reviewer"
                assert saved["meta"]["transmitted"] is False
                report = http.get(f"/runs/{run_id}/report", headers=auth(READ))
                assert str(saved["revision"]) == report.headers["X-Run-Revision"]
                assert saved["draft_text"] == meta["query_text"]
        finally:
            server.should_exit = True
            worker.join(timeout=5)
            assert not worker.is_alive()
