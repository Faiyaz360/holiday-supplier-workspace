"""Password boundaries exercised through the real HTTP application."""

import pytest
from fastapi.testclient import TestClient
from test_cloud_storage import MemoryBucket
from test_hosted_api import EXECUTE, READ, REVIEW
from test_hosted_workflow import model

from reconciliation.cloud_storage import CloudRunStore
from reconciliation.hosted_api import create_hosted_app
from reconciliation.hosted_workflow import HostedWorkflow
from reconciliation.password_access import PasswordAccess, hash_password

PASSWORD = "A fictional password for tests only!"
ORIGIN = "https://workspace.example"
HEADERS = {"Origin": ORIGIN, "X-Workspace-Request": "1"}


@pytest.fixture(scope="module")
def encoded():
    return hash_password(PASSWORD)


def client(encoded, bucket=None, clock=None):
    bucket = bucket if bucket is not None else MemoryBucket()
    auth = PasswordAccess(bucket, encoded, ORIGIN, clock=clock)
    workflow = HostedWorkflow(CloudRunStore(bucket), "SUP-PMI")
    app = create_hosted_app(
        workflow,
        read_token=READ,
        execution_token=EXECUTE,
        review_token=REVIEW,
        model_factory=model,
        password_access=auth,
    )
    return TestClient(app, base_url=ORIGIN), bucket


def login(c, password=PASSWORD, headers=HEADERS):
    return c.post("/workspace/login", json={"password": password}, headers=headers)


def test_shell_is_public_but_records_and_bearer_bypasses_are_locked(encoded):
    c, _ = client(encoded)
    assert c.get("/").status_code == 200
    for path in ["/workspace/session", "/capabilities", "/runs/" + "a" * 20]:
        assert c.get(path).status_code == 401
        assert (
            c.get(
                path,
                headers={
                    "Authorization": f"Bearer {EXECUTE}",
                    "x-goog-iap-jwt-assertion": "forged",
                },
            ).status_code
            == 401
        )
    assert (
        c.get(
            "/suppliers/SUP-PMI/payables/INV-0002", headers={"Authorization": f"Bearer {READ}"}
        ).status_code
        == 200
    )


def test_password_opens_all_features_without_separate_roles(encoded):
    c, bucket = client(encoded)
    assert login(c, "wrong").status_code == 401
    response = login(c)
    assert response.status_code == 200
    cookie = response.headers["set-cookie"].lower()
    assert "__host-holiday-session=" in cookie
    assert all(flag in cookie for flag in ["httponly", "secure", "samesite=strict", "path=/"])
    assert PASSWORD not in repr(bucket.objects)
    assert c.cookies.get("__Host-holiday-session") not in repr(bucket.objects)
    assert c.get("/capabilities").json()["permissions"] == dict.fromkeys(
        ["read", "create", "execute", "review"], True
    )
    created = c.post("/runs", json={}, headers=HEADERS)
    assert created.status_code == 200
    run_id = created.json()["run_id"]
    assert c.get(f"/runs/{run_id}").status_code == 200
    assert c.get(f"/runs/{run_id}/report").status_code == 200
    assert (
        c.post(
            f"/runs/{run_id}/execute", json={"request_id": "password-test-01"}, headers=HEADERS
        ).status_code
        == 200
    )
    assert (
        c.post(
            f"/runs/{run_id}/review", json={"reviewer": "Fictional reviewer"}, headers=HEADERS
        ).status_code
        == 200
    )


def test_sessions_survive_new_worker_but_logout_revokes_copied_cookie(encoded):
    c, bucket = client(encoded)
    assert login(c).status_code == 200
    copied = c.cookies.get("__Host-holiday-session")
    other, _ = client(encoded, bucket)
    other.cookies.set("__Host-holiday-session", copied)
    assert other.get("/workspace/session").status_code == 200
    assert c.post("/workspace/logout", json={}, headers=HEADERS).status_code == 200
    assert other.get("/capabilities").status_code == 401


def test_expiry_and_password_rotation_invalidate_sessions(encoded):
    now = [1000.0]
    c, bucket = client(encoded, clock=lambda: now[0])
    assert login(c).status_code == 200
    copied = c.cookies.get("__Host-holiday-session")
    now[0] += 3601
    assert c.get("/capabilities").status_code == 401
    now[0] = 1000
    rotated, _ = client(hash_password("Another fictional password, never deployed"), bucket)
    rotated.cookies.set("__Host-holiday-session", copied)
    assert rotated.get("/capabilities").status_code == 401


@pytest.mark.parametrize(
    "headers",
    [{}, {"Origin": "https://attacker.example", "X-Workspace-Request": "1"}, {"Origin": ORIGIN}],
)
def test_cross_site_login_and_changes_are_refused(encoded, headers):
    c, _ = client(encoded)
    assert login(c, headers=headers).status_code == 403
    assert login(c).status_code == 200
    assert c.post("/runs", json={}, headers=headers).status_code == 403
    assert c.post("/workspace/logout", json={}, headers=headers).status_code == 403


def test_rate_limit_survives_worker_restart_and_storage_errors_fail_closed(encoded):
    c, bucket = client(encoded)
    for _ in range(10):
        assert login(c, "wrong").status_code == 401
    fresh, _ = client(encoded, bucket)
    response = login(fresh)
    assert response.status_code == 429
    assert "retry-after" in response.headers

    def unavailable(*args, **kwargs):
        raise RuntimeError("private provider detail")

    bucket.get_blob = unavailable
    response = login(fresh)
    assert response.status_code == 503
    assert "private provider detail" not in response.text


def test_invalid_configuration_is_rejected(encoded):
    for origin in [
        "http://workspace.example",
        "https://workspace.example/path",
        "https://workspace.example?query=1",
    ]:
        with pytest.raises(ValueError):
            PasswordAccess(MemoryBucket(), encoded, origin)
    with pytest.raises(ValueError):
        PasswordAccess(MemoryBucket(), "plaintext", ORIGIN)


def test_session_outage_does_not_reveal_records_and_bad_cookie_is_refused(encoded):
    c, bucket = client(encoded)
    assert login(c).status_code == 200

    def unavailable(*args, **kwargs):
        raise RuntimeError("private storage details")

    bucket.get_blob = unavailable
    assert c.get("/capabilities").status_code == 503
    response = c.post("/workspace/logout", json={}, headers=HEADERS)
    assert response.status_code == 503
    assert "private storage details" not in response.text
    c.cookies.clear()
    c.cookies.set("__Host-holiday-session", "../runs/secret")
    assert c.get("/capabilities").status_code == 401


def test_rate_limit_window_recovers_without_clearing_on_restart(encoded):
    now = [1000.0]
    c, bucket = client(encoded, clock=lambda: now[0])
    for _ in range(10):
        assert login(c, "wrong").status_code == 401
    assert login(c).status_code == 429
    now[0] += 61
    fresh, _ = client(encoded, bucket, clock=lambda: now[0])
    assert login(fresh).status_code == 200


def test_forged_host_and_extra_role_fields_do_not_bypass_access(encoded):
    c, _ = client(encoded)
    assert (
        login(
            c,
            headers={
                "Origin": "https://attacker.example",
                "Host": "attacker.example",
                "X-Workspace-Request": "1",
            },
        ).status_code
        == 403
    )
    assert (
        c.post(
            "/workspace/login", json={"password": PASSWORD, "role": "admin"}, headers=HEADERS
        ).status_code
        == 422
    )
    assert (
        c.post("/workspace/session", json={"role": "analyst"}, headers=HEADERS).status_code == 405
    )


def test_factory_rejects_incomplete_password_settings_before_cloud_calls(monkeypatch, encoded):
    from google.cloud import storage

    from reconciliation.hosted_api import create_google_app

    monkeypatch.setenv("DEMO_API_TOKEN", READ)
    monkeypatch.setenv("DEMO_EXECUTION_TOKEN", EXECUTE)
    monkeypatch.setenv("DEMO_REVIEW_TOKEN", REVIEW)
    monkeypatch.setenv("DEMO_RUN_BUCKET", "fictional-private-bucket")
    monkeypatch.delenv("DEMO_IAP_AUDIENCE", raising=False)
    monkeypatch.delenv("DEMO_IAP_ROLES", raising=False)
    monkeypatch.setattr(storage, "Client", lambda: pytest.fail("Must reject before cloud call"))
    for digest, origin in [("", ORIGIN), (encoded, ""), ("plaintext", ORIGIN)]:
        monkeypatch.setenv("DEMO_PASSWORD_HASH", digest)
        monkeypatch.setenv("DEMO_WORKSPACE_ORIGIN", origin)
        with pytest.raises(ValueError):
            create_google_app()


def test_password_hashes_use_distinct_salts_and_local_http_is_opt_in(encoded):
    assert hash_password(PASSWORD) != encoded
    with pytest.raises(ValueError):
        PasswordAccess(MemoryBucket(), encoded, "http://localhost:8772")


def test_invalid_password_input_is_never_echoed_in_validation_errors(encoded):
    c, _ = client(encoded)
    oversized = "private-input-marker" * 100
    response = c.post("/workspace/login", json={"password": oversized}, headers=HEADERS)
    assert response.status_code == 422
    assert "private-input-marker" not in response.text
    response = c.post(
        "/workspace/login",
        content=r'{"password":"\ud800"}',
        headers=HEADERS | {"Content-Type": "application/json"},
    )
    assert response.status_code in {401, 422}


def test_fresh_review_preserves_previous_evidence_and_can_be_resumed(encoded):
    c, bucket = client(encoded)
    assert login(c).status_code == 200
    original = c.post("/runs", json={}, headers=HEADERS).json()["run_id"]
    assert (
        c.post(
            f"/runs/{original}/execute", json={"request_id": "original-review"}, headers=HEADERS
        ).status_code
        == 200
    )
    assert (
        c.post(
            f"/runs/{original}/review", json={"reviewer": "Fictional reviewer"}, headers=HEADERS
        ).status_code
        == 200
    )
    original_bytes = bucket.objects[f"runs/{original}.json"][1]
    response = c.post(
        "/runs/new-review", json={"request_id": "new-review-test-01"}, headers=HEADERS
    )
    assert response.status_code == 200
    fresh = response.json()["run_id"]
    assert fresh != original
    assert bucket.objects[f"runs/{original}.json"][1] == original_bytes
    data = c.get(f"/runs/{fresh}").json()
    assert data["meta"]["model_calls"] == 0
    assert data["meta"]["review"] is None
    assert data["meta"]["drafting_status"] == "offline_template"
    assert data["result"] == c.get(f"/runs/{original}").json()["result"]
    assert c.get("/workspace/session").json()["statement_ref"] == fresh
    assert (
        c.post(
            "/runs/new-review", json={"request_id": "new-review-test-01"}, headers=HEADERS
        ).json()["run_id"]
        == fresh
    )
    assert (
        c.post(
            f"/runs/{fresh}/execute",
            json={"request_id": "fresh-review-investigation"},
            headers=HEADERS,
        ).status_code
        == 200
    )
    assert (
        c.post(
            f"/runs/{fresh}/review",
            json={"reviewer": "Another fictional reviewer"},
            headers=HEADERS,
        ).status_code
        == 200
    )
    assert bucket.objects[f"runs/{original}.json"][1] == original_bytes


def test_new_review_requires_login_and_same_origin_and_rejects_extra_fields(encoded):
    c, _ = client(encoded)
    body = {"request_id": "review-boundary-01"}
    assert c.post("/runs/new-review", json=body, headers=HEADERS).status_code == 401
    assert login(c).status_code == 200
    assert c.post("/runs/new-review", json=body).status_code == 403
    assert (
        c.post("/runs/new-review", json=body | {"reset": True}, headers=HEADERS).status_code == 422
    )
