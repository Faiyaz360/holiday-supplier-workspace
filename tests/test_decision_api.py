from fastapi.testclient import TestClient
from test_cloud_storage import MemoryBucket
from test_hosted_api import EXECUTE, READ, auth

from reconciliation.cloud_storage import CloudRunStore
from reconciliation.decision_policy import rules_decisions
from reconciliation.decision_workflow import DecisionLab
from reconciliation.hosted_api import create_hosted_app
from reconciliation.hosted_workflow import HostedWorkflow


def setup():
    w = HostedWorkflow(CloudRunStore(MemoryBucket()), "SUP-PMI")
    ref = w.create()["run_id"]
    calls = []

    def provider(task, model):
        calls.append(model)
        r = rules_decisions(task)
        return r | {
            "requested_model": model,
            "cost": {"usd": 0.0001, "kind": "reported", "source": "Test"},
        }

    lab = DecisionLab(
        w,
        include_recordings=False,
        provider_runner=provider,
        gemini_runner=lambda t: provider(t, "gemini-test"),
    )
    c = TestClient(
        create_hosted_app(
            w,
            read_token=READ,
            execution_token=EXECUTE,
            review_token="review-test-credential-1234",
            decision_lab=lab,
        )
    )
    return c, w, ref, calls


def test_read_execute_permissions_replay_and_finance_unchanged():
    c, w, ref, calls = setup()
    p = f"/runs/{ref}/decisions"
    body = {"request_id": "compare-first", "case_id": "credit", "mode": "text"}
    before = w.read(ref)
    assert c.get(p).status_code == 401
    assert c.post(p, json=body, headers=auth(READ)).status_code == 403
    r = c.post(p, json=body, headers=auth(EXECUTE))
    assert r.status_code == 200, r.text
    assert len(calls) == 3
    assert not c.post(p, json=body, headers=auth(EXECUTE)).json()["claimed"]
    assert len(calls) == 3 and before == w.read(ref)
    r = c.get(p, headers=auth(READ))
    assert r.status_code == 200
    assert r.json()["summary"]["jev"]["completed"] == 1
    assert r.json()["label_status"] == "author_draft"


def test_arbitrary_models_urls_cases_and_extra_fields_rejected():
    c, w, ref, calls = setup()
    p = f"/runs/{ref}/decisions"
    for extra in [
        {"model": "evil/model"},
        {"url": "https://example.com"},
        {"expected": {"credit": True}},
    ]:
        assert (
            c.post(
                p,
                json={"request_id": "compare-first", "case_id": "credit", **extra},
                headers=auth(EXECUTE),
            ).status_code
            == 422
        )
    assert c.post(
        p, json={"request_id": "compare-first", "case_id": "nope"}, headers=auth(EXECUTE)
    ).status_code in {409, 422}
    assert not calls


def test_password_session_and_origin_protect_paid_comparison():
    from reconciliation.password_access import PasswordAccess, hash_password

    origin = "https://workspace.example"
    bucket = MemoryBucket()
    w = HostedWorkflow(CloudRunStore(bucket), "SUP-PMI")
    ref = w.create()["run_id"]
    gate = PasswordAccess(bucket, hash_password("Fictional password for this test!"), origin)
    calls = []

    def provider(task, model):
        calls.append(model)
        return rules_decisions(task) | {
            "cost": {"usd": 0.0001, "kind": "reported", "source": "Test"}
        }

    lab = DecisionLab(
        w,
        include_recordings=False,
        provider_runner=provider,
        gemini_runner=lambda t: provider(t, "gemini-test"),
    )
    c = TestClient(create_hosted_app(w, password_access=gate, decision_lab=lab), base_url=origin)
    path = f"/runs/{ref}/decisions"
    body = {"request_id": "password-comparison", "case_id": "credit"}
    assert c.post(path, json=body, headers=auth(EXECUTE)).status_code == 401
    headers = {"Origin": origin, "X-Workspace-Request": "1"}
    assert (
        c.post(
            "/workspace/login",
            json={"password": "Fictional password for this test!"},
            headers=headers,
        ).status_code
        == 200
    )
    assert c.post(path, json=body).status_code == 403
    assert (
        c.post(
            path, json=body, headers={"Origin": "https://other.example", "X-Workspace-Request": "1"}
        ).status_code
        == 403
    )
    assert not calls
    assert c.post(path, json=body, headers=headers).status_code == 200
    assert len(calls) == 3
