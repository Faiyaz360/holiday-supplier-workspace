import json
import time
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from google.auth import jwt
from google.auth.crypt.es256 import ES256Signer

from reconciliation.identity import IapIdentity, IdentityDenied

AUDIENCE = "/projects/123456789012/locations/europe-west2/services/supplier-workspace"
EMAIL = "analyst@example.com"


@pytest.fixture
def signed_identity():
    key = ec.generate_private_key(ec.SECP256R1())
    public = (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    signer = ES256Signer(key, key_id="test-key")
    calls = []

    def request(url, **kwargs):
        calls.append(url)
        return SimpleNamespace(status=200, data=json.dumps({"test-key": public}).encode())

    identity = IapIdentity(AUDIENCE, {EMAIL: "execute"}, request=request)

    def token(**changes):
        now = int(time.time())
        payload = {
            "iss": "https://cloud.google.com/iap",
            "aud": AUDIENCE,
            "iat": now - 1,
            "exp": now + 599,
            "sub": "accounts.google.com:123",
            "email": EMAIL,
        }
        payload.update(changes)
        return jwt.encode(signer, payload, header={"alg": "ES256"}).decode()

    return identity, token, calls


def test_real_signed_identity_and_public_key_cache(signed_identity):
    identity, token, calls = signed_identity
    person = identity.verify(token())
    assert person.email == EMAIL
    assert person.role == "execute"
    assert identity.verify(token()).role == "execute"
    assert len(calls) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"aud": AUDIENCE + "-other"},
        {"iss": "https://accounts.google.com"},
        {"exp": 1},
        {"iat": 9999999999},
        {"exp": 9999999999},
        {"email": "outsider@example.com"},
        {"sub": ""},
    ],
)
def test_invalid_claims_fail_closed(signed_identity, changes):
    identity, token, _ = signed_identity
    with pytest.raises(IdentityDenied):
        identity.verify(token(**changes))


def test_forged_signature_and_oversized_assertion_fail_closed(signed_identity):
    identity, token, _ = signed_identity
    parts = token().split(".")
    parts[2] = "A" * len(parts[2])
    for assertion in [".".join(parts), "x" * 9000, ""]:
        with pytest.raises(IdentityDenied):
            identity.verify(assertion)


def test_absent_configuration_preserves_legacy_mode(monkeypatch):
    monkeypatch.delenv("DEMO_IAP_AUDIENCE", raising=False)
    monkeypatch.delenv("DEMO_IAP_ROLES", raising=False)
    assert IapIdentity.from_environment() is None
    monkeypatch.setenv("DEMO_IAP_AUDIENCE", AUDIENCE)
    with pytest.raises(ValueError):
        IapIdentity.from_environment()


@pytest.mark.parametrize(
    "roles", [{}, {"*": "review"}, {EMAIL: "admin"}, {EMAIL: "read", EMAIL.upper(): "review"}]
)
def test_invalid_role_configuration_is_refused(roles):
    with pytest.raises(ValueError):
        IapIdentity(AUDIENCE, roles)


def test_identity_http_permissions_and_review_attribution(signed_identity):
    from fastapi.testclient import TestClient
    from test_cloud_storage import MemoryBucket
    from test_hosted_api import EXECUTE, READ, REVIEW, auth

    from reconciliation.cloud_storage import CloudRunStore
    from reconciliation.hosted_api import create_hosted_app
    from reconciliation.hosted_workflow import HostedWorkflow

    identity, token, _ = signed_identity
    identity.roles["reviewer@example.com"] = "review"
    workflow = HostedWorkflow(CloudRunStore(MemoryBucket()), "SUP-PMI")
    http = TestClient(
        create_hosted_app(
            workflow,
            read_token=READ,
            execution_token=EXECUTE,
            review_token=REVIEW,
            identity=identity,
        )
    )
    analyst = {
        "X-Goog-IAP-JWT-Assertion": token(),
        "Origin": "http://testserver",
        "X-Workspace-Request": "1",
    }
    reviewer = analyst | {"X-Goog-IAP-JWT-Assertion": token(email="reviewer@example.com")}
    session = http.get("/workspace/session", headers=analyst).json()
    assert session["mode"] == "google"
    assert session["email"] == EMAIL
    assert session["statement_ref"] == ""
    assert not workflow.store.bucket.objects
    created = http.post("/runs", headers=analyst, json={})
    assert created.status_code == 200
    run_id = created.json()["run_id"]
    assert http.get("/workspace/session", headers=reviewer).json()["statement_ref"] == run_id
    assert (
        http.post(
            f"/runs/{run_id}/review",
            headers=analyst | auth(REVIEW),
            json={"reviewer": "Pretend CFO"},
        ).status_code
        == 403
    )
    assert http.post("/runs", headers=reviewer, json={}).status_code == 403
    reviewed = http.post(
        f"/runs/{run_id}/review", headers=reviewer, json={"reviewer": "Pretend CFO"}
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["review"]["reviewer"] == "reviewer@example.com"
    assert (
        http.post("/workspace/session", headers=analyst, json={"role": "reviewer"}).status_code
        == 405
    )


def test_identity_mode_refuses_credential_bypass_and_cross_site(signed_identity):
    from fastapi.testclient import TestClient
    from test_cloud_storage import MemoryBucket
    from test_hosted_api import EXECUTE, READ, REVIEW, auth

    from reconciliation.cloud_storage import CloudRunStore
    from reconciliation.hosted_api import create_hosted_app
    from reconciliation.hosted_workflow import HostedWorkflow

    identity, token, _ = signed_identity
    http = TestClient(
        create_hosted_app(
            HostedWorkflow(CloudRunStore(MemoryBucket()), "SUP-PMI"),
            read_token=READ,
            execution_token=EXECUTE,
            review_token=REVIEW,
            identity=identity,
        )
    )
    assert http.get("/capabilities", headers=auth(EXECUTE)).status_code == 401
    assert (
        http.get(
            "/capabilities",
            headers={"X-Goog-Authenticated-User-Email": "accounts.google.com:" + EMAIL},
        ).status_code
        == 401
    )
    assert http.get("/suppliers/SUP-PMI/payables/INV-0002", headers=auth(READ)).status_code == 200
    assert (
        http.get("/suppliers/SUP-PMI/payables/INV-0002", headers=auth(EXECUTE)).status_code == 403
    )
    assert (
        http.get(
            "/suppliers/SUP-PMI/payables/INV-0002",
            headers=auth(READ) | {"X-Goog-IAP-JWT-Assertion": "forged"},
        ).status_code
        == 403
    )
    assertion = {"X-Goog-IAP-JWT-Assertion": token()}
    for extra in [{}, {"Origin": "https://other.example", "X-Workspace-Request": "1"}]:
        assert http.post("/runs", headers=assertion | extra, json={}).status_code == 403
    assert "const identityMode = true;" in http.get("/").text


def test_public_key_cache_expires(signed_identity, monkeypatch):
    identity, token, calls = signed_identity
    monkeypatch.setattr("reconciliation.identity.time.monotonic", lambda: 100)
    identity.verify(token())
    monkeypatch.setattr("reconciliation.identity.time.monotonic", lambda: 401)
    identity.verify(token())
    assert len(calls) == 2


def test_certificate_outage_denies_identity_without_raw_error_text(signed_identity):
    identity, token, _ = signed_identity

    def unavailable(*args, **kwargs):
        raise RuntimeError("private-transport-detail")

    identity.request.request = unavailable
    with pytest.raises(IdentityDenied) as failure:
        identity.verify(token())
    assert "private-transport-detail" not in str(failure.value)


def test_long_verified_email_is_saved_without_truncation(signed_identity):
    from fastapi.testclient import TestClient
    from test_cloud_storage import MemoryBucket
    from test_hosted_api import EXECUTE, READ, REVIEW

    from reconciliation.cloud_storage import CloudRunStore
    from reconciliation.hosted_api import create_hosted_app
    from reconciliation.hosted_workflow import HostedWorkflow

    identity, token, _ = signed_identity
    email = "reviewer@" + "b" * 60 + "." + "c" * 60 + ".example.com"
    identity.roles[email] = "review"
    workflow = HostedWorkflow(CloudRunStore(MemoryBucket()), "SUP-PMI")
    run = workflow.create()
    http = TestClient(
        create_hosted_app(
            workflow,
            read_token=READ,
            execution_token=EXECUTE,
            review_token=REVIEW,
            identity=identity,
        )
    )
    headers = {
        "X-Goog-IAP-JWT-Assertion": token(email=email),
        "Origin": "http://testserver",
        "X-Workspace-Request": "1",
    }
    result = http.post(
        f"/runs/{run['run_id']}/review",
        headers=headers,
        json={"reviewer": "Untrusted display name"},
    )
    assert result.status_code == 200
    assert result.json()["review"]["reviewer"] == email


def test_certificate_transport_failure_returns_service_unavailable(signed_identity):
    from fastapi.testclient import TestClient
    from test_cloud_storage import MemoryBucket
    from test_hosted_api import EXECUTE, READ, REVIEW

    from reconciliation.cloud_storage import CloudRunStore
    from reconciliation.hosted_api import create_hosted_app
    from reconciliation.hosted_workflow import HostedWorkflow

    identity, token, _ = signed_identity

    def unavailable(*args, **kwargs):
        raise RuntimeError("private-transport-detail")

    identity.request.request = unavailable
    http = TestClient(
        create_hosted_app(
            HostedWorkflow(CloudRunStore(MemoryBucket()), "SUP-PMI"),
            read_token=READ,
            execution_token=EXECUTE,
            review_token=REVIEW,
            identity=identity,
        )
    )
    result = http.get("/capabilities", headers={"X-Goog-IAP-JWT-Assertion": token()})
    assert result.status_code == 503
    assert "private-transport-detail" not in result.text


def test_repeated_role_assignment_is_invalid_configuration(monkeypatch):
    monkeypatch.setenv("DEMO_IAP_AUDIENCE", AUDIENCE)
    monkeypatch.setenv(
        "DEMO_IAP_ROLES", '{"analyst@example.com":"execute","analyst@example.com":"review"}'
    )
    with pytest.raises(ValueError):
        IapIdentity.from_environment()


def test_invalid_google_identity_settings_fail_before_cloud_initialisation(monkeypatch):
    from google.cloud import storage
    from test_hosted_api import EXECUTE, READ, REVIEW

    from reconciliation.hosted_api import create_google_app

    for name, value in {
        "DEMO_API_TOKEN": READ,
        "DEMO_EXECUTION_TOKEN": EXECUTE,
        "DEMO_REVIEW_TOKEN": REVIEW,
        "DEMO_RUN_BUCKET": "test-private-bucket",
        "DEMO_IAP_AUDIENCE": AUDIENCE,
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("DEMO_IAP_ROLES", raising=False)
    monkeypatch.setattr(storage, "Client", lambda: pytest.fail("Must not initialise cloud"))
    with pytest.raises(ValueError, match="configuration"):
        create_google_app()
