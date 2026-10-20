from fastapi.testclient import TestClient


def test_container_preview_stays_offline_and_enforces_separate_passes(monkeypatch):
    for name in ("DEMO_API_TOKEN", "DEMO_LIVE_APPROVED", "DEMO_API_BASE_URL"):
        monkeypatch.setenv(name, "before-preview")
    from container_smoke import EXECUTE, READ, REVIEW, auth, create_preview_app

    with TestClient(create_preview_app()) as http:
        assert http.get("/").status_code == 200
        assert http.get("/capabilities").status_code == 401
        capabilities = http.get("/capabilities", headers=auth(EXECUTE)).json()
        assert capabilities["model_mode"] == "offline_integration"
        assert capabilities["supplier_id"] == "SUP-PMI"
        assert http.post("/runs", json={}, headers=auth(READ)).status_code == 403
        run_id = http.post("/runs", json={}, headers=auth(EXECUTE)).json()["run_id"]
        assert (
            http.post(
                f"/runs/{run_id}/review", json={"reviewer": "Example"}, headers=auth(EXECUTE)
            ).status_code
            == 403
        )
        assert (
            http.get("/capabilities", headers=auth(REVIEW)).json()["permissions"]["review"] is True
        )
