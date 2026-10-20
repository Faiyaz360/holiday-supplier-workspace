import asyncio
import hashlib
from pathlib import Path

import pytest
from fastapi import HTTPException, Request
from test_hosted_api import EXECUTE, READ, auth
from test_hosted_api import client as bearer_client
from test_password_access import HEADERS, client, login
from test_password_access import encoded as encoded

ROOT = Path(__file__).resolve().parents[1]
RAW = (ROOT / "data/statements/SUP-PMI.csv").read_bytes()
CSV_HEADERS = {"Content-Type": "text/csv"}


def test_upload_requires_session_and_origin(encoded):
    c, bucket = client(encoded)
    assert c.post("/statements/preview", content=RAW, headers=CSV_HEADERS).status_code == 401
    assert c.get("/statements/sample").status_code == 401
    assert login(c).status_code == 200
    assert c.post("/statements/preview", content=RAW, headers=CSV_HEADERS).status_code == 403
    assert (
        c.post(
            "/statements/preview",
            content=RAW,
            headers={**CSV_HEADERS, **HEADERS, "Origin": "https://elsewhere.example"},
        ).status_code
        == 403
    )


def test_preview_then_import_and_reload_selects_saved_statement(encoded):
    c, bucket = client(encoded)
    login(c)
    headers = {**HEADERS, **CSV_HEADERS}
    before = set(bucket.objects)
    preview = c.post("/statements/preview", content=RAW, headers=headers)
    assert preview.status_code == 200
    data = preview.json()
    assert data["row_count"] == 101 and len(data["preview_rows"]) == 10
    assert not any(key.startswith("runs/") for key in set(bucket.objects) - before)
    assert "model_calls" not in data
    headers.update({"X-Request-ID": "import-http-request", "X-Statement-SHA256": data["digest"]})
    created = c.post("/statements/import", content=RAW, headers=headers)
    assert created.status_code == 200
    run = created.json()
    assert run["model_calls"] == 0
    assert c.get("/workspace/session").json()["statement_ref"] == run["run_id"]
    assert c.get(f"/runs/{run['run_id']}").json()["result"]["demo"] is False
    assert c.post("/statements/import", content=RAW, headers=headers).json() == run
    fresh = c.post(
        f"/runs/{run['run_id']}/new-review", json={"request_id": "new-review-http"}, headers=HEADERS
    )
    assert fresh.status_code == 200 and fresh.json()["run_id"] != run["run_id"]
    assert c.get("/workspace/session").json()["statement_ref"] == fresh.json()["run_id"]


def test_validation_messages_and_digest_change_are_useful(encoded):
    c, _ = client(encoded)
    login(c)
    headers = {
        **HEADERS,
        **CSV_HEADERS,
        "X-Request-ID": "import-http-request",
        "X-Statement-SHA256": hashlib.sha256(RAW).hexdigest(),
    }
    invalid = c.post(
        "/statements/preview", content=RAW.replace(b"239.00", b"invalid", 1), headers=headers
    )
    assert invalid.status_code == 422 and "row 3" in invalid.json()["detail"]
    changed = c.post(
        "/statements/import", content=RAW.replace(b"239.00", b"249.00", 1), headers=headers
    )
    assert changed.status_code == 409 and "preview" in changed.json()["detail"].lower()
    assert c.post("/statements/preview", content=RAW, headers=HEADERS).status_code == 415
    assert (
        c.post("/statements/preview", content=b"x" * 1_000_001, headers=headers).status_code == 413
    )
    assert c.get("/statements/sample").content == RAW


def test_read_only_bearer_cannot_import():
    c, _ = bearer_client()
    assert (
        c.post(
            "/statements/preview", content=RAW, headers={**CSV_HEADERS, **auth(READ)}
        ).status_code
        == 403
    )
    assert (
        c.post(
            "/statements/preview", content=RAW, headers={**CSV_HEADERS, **auth(EXECUTE)}
        ).status_code
        == 200
    )


def test_streamed_body_limit_ignores_false_content_length():
    from reconciliation.hosted_api import read_statement_body

    count = 0

    async def receive():
        nonlocal count
        count += 1
        if count == 1:
            return {"type": "http.request", "body": b"x" * 1_000_001, "more_body": True}
        pytest.fail("Reader consumed another chunk after exceeding its limit")

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/statements/preview",
            "headers": [(b"content-type", b"text/csv"), (b"content-length", b"1")],
        },
        receive=receive,
    )
    with pytest.raises(HTTPException) as error:
        asyncio.run(read_statement_body(request))
    assert error.value.status_code == 413 and count == 1


def test_opening_an_earlier_saved_statement_persists_selection(encoded):
    c, _ = client(encoded)
    login(c)

    def save(raw, request_id):
        return c.post(
            "/statements/import",
            content=raw,
            headers={
                **HEADERS,
                **CSV_HEADERS,
                "X-Request-ID": request_id,
                "X-Statement-SHA256": hashlib.sha256(raw).hexdigest(),
            },
        ).json()["run_id"]

    first = save(RAW, "selection-import-one")
    second = save(RAW.replace(b"239.00", b"249.00", 1), "selection-import-two")
    assert c.get("/workspace/session").json()["statement_ref"] == second
    chosen = c.post("/workspace/selection", json={"run_id": first}, headers=HEADERS)
    assert chosen.status_code == 200
    assert c.get("/workspace/session").json()["statement_ref"] == first
    bad = c.post("/workspace/selection", json={"run_id": "f" * 20}, headers=HEADERS)
    assert bad.status_code == 404
    assert c.get("/workspace/session").json()["statement_ref"] == first
    assert c.post("/workspace/selection", json={"run_id": second}).status_code == 403
