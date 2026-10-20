import json

from fastapi.testclient import TestClient
from test_agent import ScriptedModel
from test_cloud_storage import MemoryBucket
from test_hosted_api import EXECUTE, READ, auth

from reconciliation.cloud_storage import CloudRunStore
from reconciliation.hosted_api import create_hosted_app
from reconciliation.hosted_workflow import HostedWorkflow

TEXT = "For INV-0002 we issued credit note CR-PMI-25 for EUR 25.00; please confirm it is recorded."


def reply_model():
    return ScriptedModel(
        responses=[
            {"name": "get_reply", "args": {}},
            {"name": "get_invoice", "args": {"invoice_ref": "INV-0002"}},
            {
                "name": "submit_proposal",
                "args": {
                    "answer_json": json.dumps(
                        [{"evidence_id": "R-0002", "quote": TEXT, "next_action": "check_credit"}]
                    )
                },
            },
        ]
    )


def setup():
    w = HostedWorkflow(CloudRunStore(MemoryBucket()), "SUP-PMI")
    c = TestClient(
        create_hosted_app(
            w,
            read_token=READ,
            execution_token=EXECUTE,
            review_token="review-only-example-credential",
            reply_model_factory=reply_model,
        )
    )
    return c, w, w.create()["run_id"]


def test_reply_requires_access_and_confirmation_preserves_financial_evidence():
    c, w, ref = setup()
    path = f"/runs/{ref}/replies"
    body = {"request_id": "reply-api-first", "text": TEXT, "filename": "hotel.txt"}
    assert c.get(path).status_code == 401
    assert c.post(path, json=body, headers=auth(READ)).status_code == 403
    before = w.read(ref)
    analysed = c.post(path, json=body, headers=auth(EXECUTE))
    assert analysed.status_code == 200, analysed.text
    assert analysed.json()["reply"]["outcome"]["status"] == "complete"
    assert not w.cases(ref)["history"]
    repeat = c.post(path, json=body, headers=auth(EXECUTE))
    assert repeat.json()["claimed"] is False
    payload = {
        "proposal_index": 0,
        "expected_generation": 0,
        "status": "checking",
        "actor": "Fictional reviewer",
        "note": "Check the hotel credit against the ledger before concluding.",
    }
    apply = path + "/reply-api-first/apply"
    confirmed = c.post(apply, json=payload, headers=auth(EXECUTE))
    assert confirmed.status_code == 200, confirmed.text
    assert "reply-api-first" in confirmed.json()["history"][0]["note"]
    assert c.post(apply, json=payload, headers=auth(EXECUTE)).status_code == 200
    assert len(w.cases(ref)["history"]) == 1 and w.read(ref) == before
    assert (
        c.get(path, headers=auth(READ)).json()["replies"][0]["applied"]["0"]["status"] == "checking"
    )


def test_reply_limits_bad_proposals_and_unconfirmed_actions():
    c, w, ref = setup()
    path = f"/runs/{ref}/replies"
    assert (
        c.post(
            path,
            json={"request_id": "reply-bad-first", "text": "x" * 8001, "filename": "x.txt"},
            headers=auth(EXECUTE),
        ).status_code
        == 422
    )
    assert (
        c.post(
            path,
            json={
                "request_id": "reply-bad-first",
                "text": TEXT,
                "filename": "x.txt",
                "model": "unauthorised",
            },
            headers=auth(EXECUTE),
        ).status_code
        == 422
    )
    response = c.post(
        path,
        json={"request_id": "reply-good-first", "text": TEXT, "filename": "x.txt"},
        headers=auth(EXECUTE),
    )
    assert response.status_code == 200
    payload = {
        "proposal_index": 4,
        "expected_generation": 0,
        "status": "resolved",
        "actor": "User",
        "note": "A manually confirmed note",
    }
    assert (
        c.post(path + "/reply-good-first/apply", json=payload, headers=auth(EXECUTE)).status_code
        == 422
    )
    assert not w.cases(ref)["history"]


def test_comparison_is_read_only_scoped_and_has_a_labelled_example():
    c, w, ref = setup()
    before = w.read(ref)
    assert c.get(f"/runs/{ref}/comparison?previous={ref}").status_code == 401
    result = c.get(f"/runs/{ref}/comparison?previous={ref}", headers=auth(READ))
    assert result.status_code == 200, result.text
    assert result.json()["counts"]["changed"] == 0
    example = c.get("/workspace/comparison-example", headers=auth(READ))
    assert example.status_code == 200, example.text
    assert example.json()["example"] is True and example.json()["counts"]["changed"] > 0
    assert example.json()["counts"]["removed"] > 0 and example.json()["counts"]["new"] > 0
    assert w.read(ref) == before and not w.cases(ref)["history"]


def test_supplier_scope_and_proposal_conflicts_are_enforced():
    c, w, ref = setup()
    other = HostedWorkflow(w.store, "SUP-AGP").create()["run_id"]
    assert c.get(f"/runs/{ref}/comparison?previous={other}", headers=auth(READ)).status_code == 403
    assert c.get(f"/runs/{other}/replies", headers=auth(READ)).status_code == 403
    path = f"/runs/{ref}/replies"
    c.post(
        path,
        json={"request_id": "reply-stale-first", "text": TEXT, "filename": "hotel.txt"},
        headers=auth(EXECUTE),
    )
    w.case_action(
        ref,
        request_id="manual-earlier",
        evidence_id="R-0002",
        expected_generation=0,
        status="waiting",
        actor="Another user",
        note="Hotel evidence requested earlier",
    )
    payload = {
        "proposal_index": 0,
        "expected_generation": 0,
        "status": "checking",
        "actor": "User",
        "note": "Check the hotel credit.",
    }
    response = c.post(path + "/reply-stale-first/apply", json=payload, headers=auth(EXECUTE))
    assert response.status_code == 409 and len(w.cases(ref)["history"]) == 1


def test_reply_model_uses_shared_allowance_and_does_not_restart_failure():
    from test_cloud_execution import Clock

    from reconciliation.model_allowance import ModelAllowance

    bucket = MemoryBucket()
    clock = Clock()
    w = HostedWorkflow(
        CloudRunStore(bucket),
        "SUP-PMI",
        allowance=ModelAllowance(bucket, clock=clock, daily_limit=4),
    )
    ref = w.create()["run_id"]
    first = w.responses.analyse(ref, "allowance-reply-one", TEXT, "hotel.txt", model=reply_model())
    assert first["reply"]["outcome"]["model_calls"] == 3
    throttled = w.responses.analyse(
        ref, "allowance-reply-two", TEXT, "hotel.txt", model=reply_model()
    )
    assert throttled["reply"]["outcome"]["status"] == "allowance_exhausted"
    clock.now += 5
    exhausted = w.responses.analyse(
        ref, "allowance-reply-three", TEXT, "hotel.txt", model=reply_model()
    )
    assert exhausted["reply"]["outcome"]["status"] == "allowance_exhausted"
    repeated = w.responses.analyse(
        ref, "allowance-reply-three", TEXT, "hotel.txt", model=reply_model()
    )
    assert repeated["claimed"] is False and repeated["reply"]["outcome"]["model_calls"] == 0


def test_new_routes_keep_password_origin_checks():
    from test_password_access import client, hash_password, login

    c, _ = client(hash_password("A fictional password for tests only!"))
    login(c)
    from test_password_access import HEADERS

    ref = c.post("/runs", json={}, headers=HEADERS).json()["run_id"]
    body = {"request_id": "csrf-hotel-first", "filename": "hotel.txt", "text": TEXT}
    for headers in [{}, {**HEADERS, "Origin": "https://other.example"}]:
        assert c.post(f"/runs/{ref}/replies", json=body, headers=headers).status_code == 403
        assert (
            c.post(
                f"/runs/{ref}/replies/csrf-hotel-first/apply", json={}, headers=headers
            ).status_code
            == 403
        )
    assert c.get("/workspace/comparison-example").status_code == 200


def test_reply_analysis_respects_shared_request_throttle():
    from test_cloud_execution import Clock

    from reconciliation.model_allowance import ModelAllowance

    bucket = MemoryBucket()
    clock = Clock()
    w = HostedWorkflow(
        CloudRunStore(bucket), "SUP-PMI", allowance=ModelAllowance(bucket, clock=clock)
    )
    ref = w.create()["run_id"]
    w.responses.analyse(ref, "throttle-first", TEXT, "hotel.txt", model=reply_model())
    result = w.responses.analyse(ref, "throttle-second", TEXT, "hotel.txt", model=reply_model())
    assert result["reply"]["outcome"]["status"] == "rate_limited"
    assert result["reply"]["outcome"]["model_calls"] == 0
