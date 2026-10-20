from fastapi.testclient import TestClient


def preview(monkeypatch):
    from container_smoke import create_preview_app

    for key in ("DEMO_API_TOKEN", "DEMO_LIVE_APPROVED", "DEMO_API_BASE_URL"):
        monkeypatch.setenv(key, "before-test")
    return TestClient(create_preview_app(), base_url="http://localhost")


def mutate(http, role, origin="http://localhost"):
    return http.post(
        "/workspace/session",
        json={"role": role},
        headers={
            "Origin": origin,
            "X-Workspace-Request": "1",
        },
    )


def test_local_profile_opens_without_exposing_api_tokens_and_keeps_roles_separate(monkeypatch):
    from container_smoke import EXECUTE, READ, REVIEW

    with preview(monkeypatch) as http:
        assert http.get("/workspace/session").json()["available"] is True
        assert http.get("/capabilities").status_code == 401
        response = mutate(http, "analyst")
        assert response.status_code == 200
        assert all(token not in response.text for token in (READ, EXECUTE, REVIEW))
        cookie = response.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=strict" in cookie
        permissions = http.get("/capabilities").json()["permissions"]
        assert permissions["execute"] is True and permissions["review"] is False
        created = http.post(
            "/runs", json={}, headers={"Origin": "http://localhost", "X-Workspace-Request": "1"}
        )
        assert created.status_code == 200
        run_id = created.json()["run_id"]
        assert (
            http.post(
                f"/runs/{run_id}/review",
                json={"reviewer": "Person"},
                headers={"Origin": "http://localhost", "X-Workspace-Request": "1"},
            ).status_code
            == 403
        )
        assert mutate(http, "reviewer").status_code == 200
        permissions = http.get("/capabilities").json()["permissions"]
        assert permissions["review"] is True and permissions["execute"] is False


def test_local_session_refuses_cross_site_requests_and_unknown_profiles(monkeypatch):
    with preview(monkeypatch) as http:
        assert mutate(http, "analyst", "https://outside.example").status_code == 403
        assert http.post("/workspace/session", json={"role": "analyst"}).status_code == 403
        assert mutate(http, "administrator").status_code == 422
        assert mutate(http, "analyst").status_code == 200
        assert http.post("/runs", json={}).status_code == 403
        assert (
            http.post(
                "/runs",
                json={},
                headers={"Origin": "https://outside.example", "X-Workspace-Request": "1"},
            ).status_code
            == 403
        )


def test_profile_change_revokes_old_cookie_and_logout_clears_access(monkeypatch):
    with preview(monkeypatch) as http:
        assert mutate(http, "analyst").status_code == 200
        old = http.cookies.get("workspace_session")
        assert mutate(http, "reviewer").status_code == 200
        assert (
            http.get("/capabilities", headers={"Cookie": f"workspace_session={old}"}).status_code
            == 401
        )
        assert mutate(http, "signout").status_code == 200
        assert http.get("/capabilities").status_code == 401


def test_cloud_factory_does_not_expose_local_profile_sessions():
    from test_hosted_api import client

    http, _ = client()
    assert http.get("/workspace/session").status_code == 404
    assert http.post("/workspace/session", json={"role": "analyst"}).status_code == 404
    assert (
        http.get("/capabilities", headers={"Cookie": "workspace_session=anything"}).status_code
        == 401
    )


def test_expired_session_no_longer_authorises_requests(monkeypatch):
    import container_smoke

    with preview(monkeypatch) as http:
        assert mutate(http, "analyst").status_code == 200
        original_clock = container_smoke.time.monotonic
        monkeypatch.setattr(container_smoke.time, "monotonic", lambda: original_clock() + 3601)
        assert http.get("/capabilities").status_code == 401
        assert http.get("/workspace/session").json()["role"] is None


def test_local_cookie_refuses_an_untrusted_host(monkeypatch):
    with preview(monkeypatch) as http:
        assert mutate(http, "analyst").status_code == 200
        assert http.get("/capabilities", headers={"Host": "outside.example"}).status_code == 403
        assert (
            http.get("/workspace/session", headers={"Host": "outside.example"}).status_code == 403
        )
