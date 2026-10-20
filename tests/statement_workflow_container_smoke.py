"""Exercise the built Linux feature image with no external network or real keys."""

import hashlib
import json
import os
import socket
import threading
import time
from pathlib import Path

import uvicorn
from container_smoke import EXECUTE, READ, REVIEW, OfflineBucket, OfflineModel
from fastapi.testclient import TestClient
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from reconciliation.cloud_storage import CloudRunStore
from reconciliation.hosted_api import create_hosted_app
from reconciliation.hosted_workflow import HostedWorkflow
from reconciliation.model_allowance import ModelAllowance
from reconciliation.password_access import COOKIE, PasswordAccess, hash_password

ORIGIN = "https://workflow-smoke.example"
HEADERS = {"Origin": ORIGIN, "X-Workspace-Request": "1"}
PASSWORD = "Fictional feature smoke password only!"


class QuestionModel(BaseLlm):
    model: str = "offline-question-container-model"
    cursor: int = 0

    async def generate_content_async(self, llm_request, stream=False):
        self.cursor += 1
        calls = [
            {"name": "get_invoice", "args": {"invoice_ref": "INV-0002"}},
            {
                "name": "submit_answer",
                "args": {
                    "answer_json": json.dumps(
                        [{"kind": "invoice", "evidence_id": "R-0002", "focus": "comparison"}]
                    )
                },
            },
        ]
        assert self.cursor <= 2, "A verified answer must not need a model rewrite"
        yield LlmResponse(
            content=types.Content(
                role="model",
                parts=[types.Part(function_call=types.FunctionCall(**calls[self.cursor - 1]))],
            )
        )


def main():
    assert os.getuid() != 0
    assert not Path("/app/.env").exists()
    assert not os.getenv("GOOGLE_API_KEY")
    bucket = OfflineBucket()
    encoded = hash_password(PASSWORD)
    clock = [1000.0]

    def create():
        workflow = HostedWorkflow(
            CloudRunStore(bucket),
            "SUP-PMI",
            allowance=ModelAllowance(bucket, clock=lambda: clock[0]),
        )
        app = create_hosted_app(
            workflow,
            read_token=READ,
            execution_token=EXECUTE,
            review_token=REVIEW,
            password_access=PasswordAccess(bucket, encoded, ORIGIN),
            model_factory=OfflineModel,
            question_model_factory=QuestionModel,
        )
        return app, workflow

    app, workflow = create()
    os.environ["DEMO_API_TOKEN"] = READ
    os.environ["DEMO_LIVE_APPROVED"] = "0"
    raw = Path("/app/data/statements/SUP-PMI.csv").read_bytes().replace(b"239.00", b"249.00", 1)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        os.environ["DEMO_API_BASE_URL"] = f"http://127.0.0.1:{listener.getsockname()[1]}"
        server = uvicorn.Server(uvicorn.Config(app, access_log=False, log_level="critical"))
        worker = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        worker.start()
        try:
            for _ in range(150):
                if server.started:
                    break
                time.sleep(0.02)
            assert server.started
            with TestClient(app, base_url=ORIGIN) as http:
                assert http.get("/statements/sample").status_code == 401
                assert (
                    http.post(
                        "/workspace/login", json={"password": PASSWORD}, headers=HEADERS
                    ).status_code
                    == 200
                )
                cookie = http.cookies.get(COOKIE)
                preview = http.post(
                    "/statements/preview",
                    content=raw,
                    headers=HEADERS | {"Content-Type": "text/csv"},
                )
                assert preview.status_code == 200, preview.text
                assert preview.json()["row_count"] == 101
                assert not any(key.startswith("runs/") for key in bucket.objects)
                imported = http.post(
                    "/statements/import",
                    content=raw,
                    headers=HEADERS
                    | {
                        "Content-Type": "text/csv",
                        "X-Request-ID": "linux-import-01",
                        "X-Statement-SHA256": preview.json()["digest"],
                    },
                )
                assert imported.status_code == 200, imported.text
                run = imported.json()["run_id"]
                source = json.loads(bucket.objects[f"runs/{run}.json"][1])
                assert source["schema_version"] == 2
                assert source["files"]["source-statement.csv"].encode("utf-8") == raw
                assert http.get("/workspace/session").json()["statement_ref"] == run
                investigation = http.post(
                    f"/runs/{run}/execute",
                    json={"request_id": "linux-investigate-01"},
                    headers=HEADERS,
                )
                assert investigation.status_code == 200, investigation.text
                assert investigation.json()["meta"]["drafting_status"] == "offline_model_verified"
                assert investigation.json()["meta"]["evidence_source"] == "simulated_finance_api"
                assert "EUR 249.00" in investigation.json()["meta"]["query_text"]
                clock[0] += 6
                response = http.post(
                    f"/runs/{run}/questions",
                    json={"request_id": "linux-question-01", "question": "Explain INV-0002"},
                    headers=HEADERS,
                )
                assert response.status_code == 200, response.text
                outcome = response.json()["question"]["outcome"]
                assert outcome["status"] == "complete" and outcome["model_calls"] == 2
                assert "EUR 249.00" in outcome["blocks"][0]["text"]
                assert "EUR 214.00" in outcome["blocks"][0]["text"]
                assert outcome["blocks"][0]["evidence_ids"] == ["R-0002"]
                repeated = http.post(
                    f"/runs/{run}/questions",
                    json={"request_id": "linux-question-01", "question": "Explain INV-0002"},
                    headers=HEADERS,
                )
                assert repeated.status_code == 200 and repeated.json()["claimed"] is False
                assert (
                    http.post(
                        f"/runs/{run}/review",
                        json={"reviewer": "Linux verification (fictional records)"},
                        headers=HEADERS,
                    ).status_code
                    == 200
                )
                original = bucket.objects[f"runs/{run}.json"][1]
                board = http.get(f"/runs/{run}/cases").json()
                assert len(board["rows"]) == 5 and board["history"] == []
                case = http.post(
                    f"/runs/{run}/cases/R-0002/actions",
                    json={
                        "request_id": "linux-case-01",
                        "expected_generation": 0,
                        "status": "waiting",
                        "actor": "Linux verification",
                        "note": "Hotel asked to confirm balance",
                    },
                    headers=HEADERS,
                )
                assert (
                    case.status_code == 200
                    and case.json()["cases"]["R-0002"]["status"] == "waiting"
                )
                quality = http.get(f"/runs/{run}/quality")
                assert quality.status_code == 200
                assert quality.json()["current"]["checks"]["factual_query"] == "passed"
                assert (
                    quality.json()["historical"]["metrics"]["response_match_score"]["passed"] == 1
                )
                assert bucket.objects[f"runs/{run}.json"][1] == original

                clock[0] += 6
                read_only = http.post(
                    f"/runs/{run}/questions",
                    json={"request_id": "linux-question-reviewed", "question": "Explain INV-0002"},
                    headers=HEADERS,
                )
                assert (
                    read_only.status_code == 200
                    and read_only.json()["question"]["outcome"]["status"] == "complete"
                )
                assert bucket.objects[f"runs/{run}.json"][1] == original
                for name, step, state in [
                    ("finance_outage", "start", "provider_error"),
                    ("finance_outage", "retry", "recovered"),
                    ("hostile_note", "start", "scope_preserved"),
                ]:
                    example = http.post(
                        "/workspace/recovery-example",
                        json={"name": name, "step": step},
                        headers=HEADERS,
                    )
                    assert example.status_code == 200 and example.json()["state"] == state
                    assert example.json()["evidence_preserved"] is True
                assert bucket.objects[f"runs/{run}.json"][1] == original
                fresh = http.post(
                    f"/runs/{run}/new-review",
                    json={"request_id": "linux-new-review-01"},
                    headers=HEADERS,
                )
                assert fresh.status_code == 200 and fresh.json()["run_id"] != run
                new_ref = fresh.json()["run_id"]
                assert (
                    http.get(f"/runs/{new_ref}").json()["result"]
                    == http.get(f"/runs/{run}").json()["result"]
                )
                assert (
                    http.post(
                        "/workspace/selection", json={"run_id": run}, headers=HEADERS
                    ).status_code
                    == 200
                )
                fresh_app, fresh_workflow = create()
                with TestClient(fresh_app, base_url=ORIGIN) as recovered:
                    recovered.cookies.set(COOKIE, cookie)
                    assert recovered.get("/workspace/session").json()["statement_ref"] == run
                    assert len(recovered.get(f"/runs/{run}/conversation").json()["questions"]) == 2
                    assert fresh_workflow.read(run)["meta"]["review"] is not None
                    assert (
                        recovered.get(f"/runs/{run}/cases").json()["history"]
                        == case.json()["history"]
                    )
                    assert recovered.get(f"/runs/{run}/evidence").status_code == 200
                    assert (
                        hashlib.sha256(recovered.get(f"/runs/{run}/report").content).hexdigest()
                        == fresh_workflow.read(run)["meta"]["report_sha256"]
                    )
                assert http.post("/workspace/logout", json={}, headers=HEADERS).status_code == 200
                http.cookies.set(COOKIE, cookie)
                assert http.get(f"/runs/{run}/conversation").status_code == 401
                assert PASSWORD not in repr(bucket.objects) and cookie not in repr(bucket.objects)
            print(
                json.dumps(
                    {
                        "status": "passed",
                        "runtime_uid": os.getuid(),
                        "follow_up": True,
                        "quality": True,
                        "case_history_recovered": True,
                        "upload": True,
                        "exact_sources": True,
                        "draft": True,
                        "grounded_questions": True,
                        "question_calls": 2,
                        "review_immutable": True,
                        "recovery_examples": 3,
                        "fresh_client_recovery": True,
                        "selection_persists": True,
                        "logout_revoked": True,
                        "finance_lookup": "real loopback HTTP",
                        "model": "injected offline model",
                        "external_network": "disabled",
                        "live_model_requests": 0,
                        "cloud_requests": 0,
                    }
                )
            )
        finally:
            server.should_exit = True
            worker.join(5)


if __name__ == "__main__":
    main()
