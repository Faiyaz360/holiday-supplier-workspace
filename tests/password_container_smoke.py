"""Run the password-enabled Linux image end to end with no external network."""

import json
import os
import socket
import threading
import time

import uvicorn
from container_smoke import EXECUTE, READ, REVIEW, OfflineBucket, OfflineModel
from fastapi.testclient import TestClient

from reconciliation.cloud_storage import CloudRunStore
from reconciliation.hosted_api import create_hosted_app
from reconciliation.hosted_workflow import HostedWorkflow
from reconciliation.password_access import COOKIE, PasswordAccess, hash_password

ORIGIN = "https://password-smoke.example"
HEADERS = {"Origin": ORIGIN, "X-Workspace-Request": "1"}
PASSWORD = "Fictional Linux smoke password only!"


def main():
    assert os.getuid() != 0
    assert not os.path.exists("/app/.env")
    bucket = OfflineBucket()
    encoded = hash_password(PASSWORD)
    workflow = HostedWorkflow(CloudRunStore(bucket), "SUP-PMI")
    gate = PasswordAccess(bucket, encoded, ORIGIN)
    app = create_hosted_app(
        workflow,
        read_token=READ,
        execution_token=EXECUTE,
        review_token=REVIEW,
        password_access=gate,
        model_factory=OfflineModel,
    )
    os.environ["DEMO_API_TOKEN"] = READ
    os.environ["DEMO_LIVE_APPROVED"] = "0"
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
                assert http.get("/").status_code == 200
                assert http.get("/capabilities").status_code == 401
                assert (
                    http.post(
                        "/workspace/login", json={"password": PASSWORD}, headers=HEADERS
                    ).status_code
                    == 200
                )
                cookie = http.cookies.get(COOKIE)
                assert http.get("/capabilities").json()["permissions"] == dict.fromkeys(
                    ["read", "create", "execute", "review"], True
                )
                run_id = http.post("/runs", json={}, headers=HEADERS).json()["run_id"]
                response = http.post(
                    f"/runs/{run_id}/execute",
                    json={"request_id": "linux-password-smoke-01"},
                    headers=HEADERS,
                )
                assert response.status_code == 200, response.text
                meta = response.json()["meta"]
                assert meta["drafting_status"] == "offline_model_verified"
                assert meta["evidence_source"] == "simulated_finance_api"
                assert meta["model_calls"] == 3
                assert (
                    http.post(
                        f"/runs/{run_id}/review",
                        json={"reviewer": "Fictional Linux reviewer"},
                        headers=HEADERS,
                    ).status_code
                    == 200
                )
                assert http.get(f"/runs/{run_id}/report").status_code == 200
                original_bytes = bucket.objects[f"runs/{run_id}.json"][1]
                fresh = http.post(
                    "/runs/new-review",
                    json={"request_id": "linux-fresh-review-01"},
                    headers=HEADERS,
                )
                assert fresh.status_code == 200
                fresh_id = fresh.json()["run_id"]
                assert fresh_id != run_id
                assert http.get("/workspace/session").json()["statement_ref"] == fresh_id
                assert http.get(f"/runs/{fresh_id}").json()["meta"]["review"] is None
                assert bucket.objects[f"runs/{run_id}.json"][1] == original_bytes
                assert http.post("/workspace/logout", json={}, headers=HEADERS).status_code == 200
                http.cookies.set(COOKIE, cookie)
                assert http.get("/capabilities").status_code == 401
                assert PASSWORD not in repr(bucket.objects)
                assert cookie not in repr(bucket.objects)
            print(
                json.dumps(
                    {
                        "status": "passed",
                        "runtime_uid": os.getuid(),
                        "password_hash": "scrypt-v1",
                        "password_login": True,
                        "full_permissions": True,
                        "logout_revoked": True,
                        "fresh_review_preserves_previous": True,
                        "adk_calls": 3,
                        "finance_lookup": "real loopback HTTP",
                        "storage": "in-memory SDK boundary",
                        "model": "offline stand-in",
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
