"""Run the Linux image's complete HTTP/ADK path using offline test doubles only.

Mount this single file read-only at /app/container_smoke.py, then run it with
Python inside the built image. It is not copied into the production image.
"""

import argparse
import json
import os
import secrets
import socket
import threading
import time
from typing import Literal

import httpx
import uvicorn
from fastapi import Request
from fastapi.responses import JSONResponse
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.api_core.exceptions import PreconditionFailed
from google.genai import types
from pydantic import BaseModel, ConfigDict

from reconciliation.cloud_storage import CloudRunStore
from reconciliation.hosted_api import create_hosted_app
from reconciliation.hosted_workflow import HostedWorkflow
from reconciliation.report import INTRODUCTIONS

READ = "container-view-pass-fictional"
EXECUTE = "container-execute-pass-fictional"
REVIEW = "container-review-pass-fictional"


class OfflineBucket:
    def __init__(self):
        self.objects = {}
        self.lock = threading.Lock()

    def blob(self, name):
        return OfflineBlob(self, name)

    def get_blob(self, name, **kwargs):
        with self.lock:
            if name not in self.objects:
                return None
            blob = self.blob(name)
            blob.generation, data = self.objects[name]
            blob.size = len(data)
            return blob


class OfflineBlob:
    def __init__(self, bucket, name):
        self.bucket, self.name = bucket, name
        self.generation = None

    def upload_from_string(self, data, *, if_generation_match, **kwargs):
        with self.bucket.lock:
            revision = self.bucket.objects.get(self.name, (0, b""))[0]
            if revision != if_generation_match:
                raise PreconditionFailed("Stale test revision")
            self.generation = revision + 1
            self.bucket.objects[self.name] = (self.generation, data.encode("utf-8"))

    def download_as_bytes(self, *, if_generation_match, **kwargs):
        with self.bucket.lock:
            revision, data = self.bucket.objects[self.name]
            if revision != if_generation_match:
                raise PreconditionFailed("Stale test revision")
            return data


class OfflineModel(BaseLlm):
    model: str = "offline-container-test-model"
    cursor: int = 0

    async def generate_content_async(self, llm_request, stream=False):
        calls = [
            {"name": "lookup_payable", "args": {"invoice_ref": "INV-0002"}},
            {
                "name": "propose_query",
                "args": {
                    "exception_ids": ["R-0002", "R-0003", "R-0004", "R-0100", "R-0101"],
                    "introduction": INTRODUCTIONS[0],
                },
            },
        ]
        self.cursor += 1
        part = (
            types.Part(function_call=types.FunctionCall(**calls[self.cursor - 1]))
            if self.cursor <= len(calls)
            else types.Part(text="Draft ready for review.")
        )
        yield LlmResponse(content=types.Content(role="model", parts=[part]))


REPLY_TEXT = (
    "For INV-0002 we issued credit note CR-PMI-25 for EUR 25.00; please confirm it is recorded."
)


class OfflineReplyModel(BaseLlm):
    model: str = "offline-reply-test-model"
    cursor: int = 0

    async def generate_content_async(self, llm_request, stream=False):
        calls = [
            {"name": "get_reply", "args": {}},
            {"name": "get_invoice", "args": {"invoice_ref": "INV-0002"}},
            {
                "name": "submit_proposal",
                "args": {
                    "answer_json": json.dumps(
                        [
                            {
                                "evidence_id": "R-0002",
                                "quote": REPLY_TEXT,
                                "next_action": "check_credit",
                            }
                        ]
                    )
                },
            },
        ]
        self.cursor += 1
        part = (
            types.Part(function_call=types.FunctionCall(**calls[self.cursor - 1]))
            if self.cursor <= len(calls)
            else types.Part(text="Proposal ready")
        )
        yield LlmResponse(content=types.Content(role="model", parts=[part]))


def auth(token):
    return {"Authorization": f"Bearer {token}"}


class LocalProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["analyst", "reviewer", "signout"]


def attach_local_sessions(app, statement_ref):
    """Local preview convenience only; never installed by the Google app factory."""
    sessions = {}
    profiles = {"analyst": (EXECUTE, "Accounts analyst"), "reviewer": (REVIEW, "Finance reviewer")}

    def same_origin(request):
        return (
            request.url.hostname in {"localhost", "127.0.0.1", "::1"}
            and request.headers.get("origin") == str(request.base_url).rstrip("/")
            and request.headers.get("x-workspace-request") == "1"
        )

    @app.middleware("http")
    async def local_session_context(request, call_next):
        now = time.monotonic()
        for key in list(sessions):
            if sessions[key][1] <= now:
                sessions.pop(key, None)
        is_session = request.url.path == "/workspace/session"
        cookie = request.cookies.get("workspace_session", "")
        if is_session and request.method == "POST" and not same_origin(request):
            return JSONResponse({"detail": "Workspace request not allowed"}, status_code=403)
        if cookie in sessions and not request.headers.get("authorization") and not is_session:
            if request.url.hostname not in {"localhost", "127.0.0.1", "::1"} or (
                request.method not in {"GET", "HEAD", "OPTIONS"} and not same_origin(request)
            ):
                return JSONResponse({"detail": "Workspace request not allowed"}, status_code=403)
            role = sessions[cookie][0]
            request.scope["headers"] = list(request.scope["headers"]) + [
                (b"authorization", ("Bearer " + profiles[role][0]).encode())
            ]
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/workspace/session")
    def local_profile(request: Request):
        if request.url.hostname not in {"localhost", "127.0.0.1", "::1"}:
            return JSONResponse({"detail": "Local workspace only"}, status_code=403)
        current = sessions.get(request.cookies.get("workspace_session", ""))
        role = current[0] if current else None
        return {
            "available": True,
            "role": role,
            "name": profiles[role][1] if role else None,
            "statement_ref": statement_ref if role else None,
        }

    @app.post("/workspace/session")
    def change_local_profile(request: Request, body: LocalProfileRequest):
        sessions.pop(request.cookies.get("workspace_session", ""), None)
        if body.role == "signout":
            response = JSONResponse({"available": True, "role": None, "name": None})
            response.delete_cookie("workspace_session", path="/", httponly=True, samesite="strict")
            return response
        if len(sessions) >= 256:
            sessions.pop(next(iter(sessions)))
        cookie = secrets.token_urlsafe(32)
        sessions[cookie] = (body.role, time.monotonic() + 3600)
        response = JSONResponse(
            {
                "available": True,
                "role": body.role,
                "name": profiles[body.role][1],
                "statement_ref": statement_ref,
            }
        )
        response.set_cookie(
            "workspace_session", cookie, max_age=3600, path="/", httponly=True, samesite="strict"
        )
        return response


def create_preview_app():
    workflow = HostedWorkflow(CloudRunStore(OfflineBucket()), "SUP-PMI")
    app = create_hosted_app(
        workflow,
        read_token=READ,
        execution_token=EXECUTE,
        review_token=REVIEW,
        model_factory=OfflineModel,
        reply_model_factory=OfflineReplyModel,
    )
    attach_local_sessions(app, workflow.create()["run_id"])
    os.environ["DEMO_API_TOKEN"] = READ
    os.environ["DEMO_LIVE_APPROVED"] = "0"
    os.environ["DEMO_API_BASE_URL"] = "http://127.0.0.1:8080"
    return app


def main():
    uid = os.getuid() if hasattr(os, "getuid") else None
    assert uid != 0, "The image must use its unprivileged runtime user"
    app = create_preview_app()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        server = uvicorn.Server(
            uvicorn.Config(app, access_log=False, log_level="critical", ws="none")
        )
        worker = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        worker.start()
        try:
            for _ in range(150):
                if server.started:
                    break
                time.sleep(0.02)
            assert server.started, "The image did not start its local HTTP service"
            url = f"http://127.0.0.1:{listener.getsockname()[1]}"
            os.environ["DEMO_API_BASE_URL"] = url
            with httpx.Client(base_url=url, timeout=30, trust_env=False) as http:
                assert http.get("/").status_code == 200
                assert http.get("/capabilities").status_code == 401
                assert http.post("/runs", json={}, headers=auth(READ)).status_code == 403
                created = http.post("/runs", json={}, headers=auth(EXECUTE))
                assert created.status_code == 200
                run_id = created.json()["run_id"]
                executed = http.post(
                    f"/runs/{run_id}/execute",
                    headers=auth(EXECUTE),
                    json={"request_id": "container-offline-request"},
                )
                assert executed.status_code == 200
                meta = executed.json()["meta"]
                assert meta["drafting_status"] == "offline_model_verified"
                assert meta["evidence_source"] == "simulated_finance_api"
                assert meta["model_calls"] == 3
                query = meta["query_text"]
                assert "EUR 239.00" in query and "EUR 214.00" in query
                assert all(
                    key in query for key in ["R-0002", "R-0003", "R-0004", "R-0100", "R-0101"]
                )
                assert (
                    http.post(
                        f"/runs/{run_id}/review",
                        headers=auth(EXECUTE),
                        json={"reviewer": "Example"},
                    ).status_code
                    == 403
                )
                reviewed = http.post(
                    f"/runs/{run_id}/review",
                    headers=auth(REVIEW),
                    json={"reviewer": "Fictional container reviewer"},
                )
                assert reviewed.status_code == 200
                saved = http.get(f"/runs/{run_id}", headers=auth(READ)).json()
                assert saved["meta"]["review"]["reviewer"] == "Fictional container reviewer"
                assert saved["meta"]["transmitted"] is False
                assert saved["draft_text"] == query
                assert "Subject: Invoice queries" in saved["supplier_email"]
                assert "Statement: EUR 239.00" in saved["supplier_email"]
                assert "Our ledger: EUR 214.00" in saved["supplier_email"]
                assert "R-0002 |" not in saved["supplier_email"]
                report = http.get(f"/runs/{run_id}/report", headers=auth(READ))
                assert report.status_code == 200
                assert report.headers["X-Run-Revision"] == str(saved["revision"])
                assert "EUR 239.00" in report.text and "EUR 214.00" in report.text
                assert "Reviewed locally" in report.text
                assert "container-execute-pass-fictional" not in report.text
                # New workflows use the same protected HTTP boundary and immutable report.
                before_report = http.get(f"/runs/{run_id}/report", headers=auth(READ)).content
                reply_response = http.post(
                    f"/runs/{run_id}/replies",
                    headers=auth(EXECUTE),
                    json={
                        "request_id": "container-reply-first",
                        "filename": "hotel.txt",
                        "text": REPLY_TEXT,
                    },
                )
                assert reply_response.status_code == 200, reply_response.text
                assert reply_response.json()["reply"]["outcome"]["status"] == "complete"
                confirmed = http.post(
                    f"/runs/{run_id}/replies/container-reply-first/apply",
                    headers=auth(EXECUTE),
                    json={
                        "proposal_index": 0,
                        "expected_generation": 0,
                        "status": "checking",
                        "actor": "Container verifier",
                        "note": "Fictional check: inspect the credit before deciding.",
                    },
                )
                assert confirmed.status_code == 200, confirmed.text
                assert (
                    http.get(f"/runs/{run_id}/report", headers=auth(READ)).content == before_report
                )
                example = http.get("/workspace/comparison-example", headers=auth(READ))
                assert example.status_code == 200 and example.json()["example"] is True
                assert example.json()["counts"]["new"] == 1
                assert example.json()["counts"]["removed"] == 1
                print(
                    json.dumps(
                        {
                            "status": "passed",
                            "runtime_uid": uid,
                            "storage": "in-memory SDK boundary",
                            "model": "offline stand-in",
                            "adk_calls": meta["model_calls"],
                            "finance_lookup": "real loopback HTTP",
                            "review_persisted": True,
                            "reply_confirmation_verified": True,
                            "comparison_example_verified": True,
                            "live_model_requests": 0,
                            "cloud_requests": 0,
                        }
                    )
                )
        finally:
            server.should_exit = True
            worker.join(timeout=5)
            assert not worker.is_alive(), "Test server did not stop"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Explicitly offline local image check/preview")
    parser.add_argument("--serve", action="store_true", help="Keep a local offline preview running")
    if parser.parse_args().serve:
        uvicorn.run(create_preview_app(), host="0.0.0.0", port=8080, access_log=False, ws="none")
    else:
        main()
