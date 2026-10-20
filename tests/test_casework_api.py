from test_hosted_api import READ, auth
from test_hosted_api import client as bearer_client
from test_password_access import encoded as encoded
from test_upload_api import HEADERS, client, login


def test_case_and_quality_require_password_and_origin_and_preserve_report(encoded):
    c, _ = client(encoded)
    assert c.get("/runs/" + "a" * 20 + "/cases").status_code == 401
    login(c)
    ref = c.post("/runs", json={}, headers=HEADERS).json()["run_id"]
    report = c.get(f"/runs/{ref}/report").content
    board = c.get(f"/runs/{ref}/cases")
    assert board.status_code == 200 and len(board.json()["rows"]) == 5
    body = {
        "request_id": "case-api-request",
        "expected_generation": 0,
        "status": "waiting",
        "actor": "Release test",
        "note": "Hotel contacted; awaiting confirmation",
    }
    path = f"/runs/{ref}/cases/R-0002/actions"
    assert c.post(path, json=body).status_code == 403
    saved = c.post(path, json=body, headers=HEADERS)
    assert saved.status_code == 200 and saved.json()["cases"]["R-0002"]["status"] == "waiting"
    assert c.post(path, json=body, headers=HEADERS).json() == saved.json()
    assert (
        c.post(path, json=body | {"request_id": "stale-case-02"}, headers=HEADERS).status_code
        == 409
    )
    assert (
        c.post(
            f"/runs/{ref}/cases/R-0001/actions",
            json=body | {"expected_generation": 1},
            headers=HEADERS,
        ).status_code
        == 422
    )
    assert c.get(f"/runs/{ref}/report").content == report
    quality = c.get(f"/runs/{ref}/quality")
    assert quality.status_code == 200
    assert quality.json()["current"]["checks"]["factual_query"] == "not_run"
    assert quality.json()["historical"]["metrics"]["response_match_score"]["passed"] == 1
    assert (
        c.post(
            path, content=b"x" * 8001, headers=HEADERS | {"Content-Type": "application/json"}
        ).status_code
        == 413
    )
    assert c.post(path, json=body | {"raw_path": "../../bad"}, headers=HEADERS).status_code == 422


def test_read_only_service_credential_cannot_record_follow_up():
    c, w = bearer_client()
    ref = w.create()["run_id"]
    response = c.post(
        f"/runs/{ref}/cases/R-0002/actions",
        headers=auth(READ),
        json={
            "request_id": "readonly-case",
            "expected_generation": 0,
            "status": "resolved",
            "actor": "x",
            "note": "x",
        },
    )
    assert response.status_code == 403
