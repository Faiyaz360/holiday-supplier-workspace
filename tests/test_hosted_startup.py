"""Hosted startup checks without Google credentials or paid requests."""

import sys

import pytest
from fastapi.testclient import TestClient
from test_hosted_api import client


def test_hosted_command_uses_the_full_app_and_cloud_port(monkeypatch):
    import dotenv
    import uvicorn

    from reconciliation import __main__, hosted_api

    app = client()[0].app
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setattr(hosted_api, "create_google_app", lambda: app)
    monkeypatch.setenv("PORT", "9071")
    monkeypatch.setattr(sys, "argv", ["reconciliation", "serve-hosted", "--host", "0.0.0.0"])
    observed = {}

    def start(actual_app, **kwargs):
        observed.update(kwargs)
        with TestClient(actual_app) as http:
            assert http.get("/").status_code == 200
            assert http.get("/runs/" + "0" * 20).status_code == 401
            assert http.get("/health").status_code == 200

    monkeypatch.setattr(uvicorn, "run", start)
    assert __main__.main() == 0
    assert observed["host"] == "0.0.0.0"
    assert observed["port"] == 9071


@pytest.mark.parametrize("port", ["not-a-port", "0", "65536"])
def test_hosted_command_rejects_bad_cloud_port_without_starting(monkeypatch, port):
    import dotenv

    from reconciliation import __main__, hosted_api

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setenv("PORT", port)
    monkeypatch.setattr(sys, "argv", ["reconciliation", "serve-hosted"])
    monkeypatch.setattr(hosted_api, "create_google_app", lambda: pytest.fail("Must not start"))
    with pytest.raises(SystemExit) as error:
        __main__.main()
    assert error.value.code == 2


def test_hosted_startup_rejects_missing_passes_without_cloud_initialisation(monkeypatch, capsys):
    import dotenv
    from google.cloud import storage

    from reconciliation import __main__

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: None)
    for name in ["DEMO_API_TOKEN", "DEMO_EXECUTION_TOKEN", "DEMO_REVIEW_TOKEN"]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("PORT", raising=False)
    monkeypatch.setattr(storage, "Client", lambda: pytest.fail("Must not initialise cloud"))
    monkeypatch.setattr(sys, "argv", ["reconciliation", "serve-hosted"])
    assert __main__.main() == 1
    assert "Traceback" not in capsys.readouterr().err


def test_non_server_commands_ignore_an_invalid_hosting_port(monkeypatch, tmp_path):
    import dotenv

    from reconciliation import __main__

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setenv("PORT", "not-a-port")
    monkeypatch.setattr(sys, "argv", ["reconciliation", "demo", "--output", str(tmp_path)])
    assert __main__.main() == 0


def test_google_factory_uses_default_identity_and_the_configured_private_bucket(monkeypatch):
    from google.cloud import storage
    from test_cloud_storage import MemoryBucket
    from test_hosted_api import EXECUTE, READ, REVIEW, auth

    from reconciliation.hosted_api import create_google_app

    for name, value in {
        "DEMO_API_TOKEN": READ,
        "DEMO_EXECUTION_TOKEN": EXECUTE,
        "DEMO_REVIEW_TOKEN": REVIEW,
        "DEMO_SUPPLIER_ID": "SUP-PMI",
        "DEMO_RUN_BUCKET": "fictional-holiday-run-tests",
        "DEMO_LIVE_APPROVED": "0",
    }.items():
        monkeypatch.setenv(name, value)
    observed = []
    bucket = MemoryBucket()

    class DefaultIdentityClient:
        def bucket(self, name):
            observed.append(name)
            return bucket

    monkeypatch.setattr(storage, "Client", DefaultIdentityClient)
    with TestClient(create_google_app()) as http:
        assert http.get("/").status_code == 200
        run_id = http.post("/runs", json={}, headers=auth(EXECUTE)).json()["run_id"]
        assert http.get(f"/runs/{run_id}", headers=auth(READ)).status_code == 200
        assert http.get("/capabilities", headers=auth(EXECUTE)).json()["execution_enabled"] is False
    assert observed == ["fictional-holiday-run-tests"]
    assert len(bucket.objects) == 1


def test_google_factory_sanitises_identity_errors(monkeypatch):
    from google.cloud import storage
    from test_hosted_api import EXECUTE, READ, REVIEW

    from reconciliation.hosted_api import create_google_app

    for name, value in {
        "DEMO_API_TOKEN": READ,
        "DEMO_EXECUTION_TOKEN": EXECUTE,
        "DEMO_REVIEW_TOKEN": REVIEW,
        "DEMO_RUN_BUCKET": "fictional-holiday-run-tests",
    }.items():
        monkeypatch.setenv(name, value)

    def unavailable():
        raise RuntimeError("private-identity-details-must-not-appear")

    monkeypatch.setattr(storage, "Client", unavailable)
    with pytest.raises(ValueError) as error:
        create_google_app()
    assert str(error.value) == "Google cloud identity is unavailable"
    assert "private-identity" not in str(error.value)
