from fastapi.testclient import TestClient
from test_cloud_execution import Clock
from test_cloud_storage import MemoryBucket
from test_hosted_api import EXECUTE, READ, REVIEW, auth
from test_hosted_workflow import model as draft_model
from test_question_agent import model as question_model

from reconciliation.cloud_storage import CloudRunStore
from reconciliation.hosted_api import create_hosted_app
from reconciliation.hosted_workflow import HostedWorkflow
from reconciliation.model_allowance import ModelAllowance


def setup():
    bucket = MemoryBucket()
    clock = Clock()
    workflow = HostedWorkflow(
        CloudRunStore(bucket), "SUP-PMI", allowance=ModelAllowance(bucket, clock=clock)
    )
    app = create_hosted_app(
        workflow,
        read_token=READ,
        execution_token=EXECUTE,
        review_token=REVIEW,
        model_factory=draft_model,
        question_model_factory=lambda: question_model(
            [{"kind": "invoice", "evidence_id": "R-0002", "focus": "comparison"}]
        ),
    )
    return TestClient(app), workflow, clock


def test_question_endpoints_persist_outcome_without_rerunning_or_changing_review():
    http, workflow, clock = setup()
    run = http.post("/runs", json={}, headers=auth(EXECUTE)).json()["run_id"]
    assert http.get(f"/runs/{run}/conversation").status_code == 401
    assert (
        http.post(
            f"/runs/{run}/questions",
            json={"request_id": "question-one", "question": "Explain INV-0002"},
            headers=auth(READ),
        ).status_code
        == 403
    )
    before = workflow.read(run)
    response = http.post(
        f"/runs/{run}/questions",
        json={"request_id": "question-one", "question": "Explain INV-0002"},
        headers=auth(EXECUTE),
    )
    assert response.status_code == 200
    assert response.json()["question"]["outcome"]["status"] == "complete"
    assert "EUR 239.00" in response.json()["question"]["outcome"]["blocks"][0]["text"]
    repeat = http.post(
        f"/runs/{run}/questions",
        json={"request_id": "question-one", "question": "Explain INV-0002"},
        headers=auth(EXECUTE),
    )
    assert repeat.status_code == 200 and repeat.json()["claimed"] is False
    assert len(http.get(f"/runs/{run}/conversation", headers=auth(READ)).json()["questions"]) == 1
    assert workflow.read(run) == before
    assert "conversation" not in repr(before["meta"])


def test_questions_and_investigation_share_the_same_allowance():
    http, workflow, clock = setup()
    run = workflow.create()["run_id"]
    result = workflow.execute(run, "investigation-one", model=draft_model())
    assert result["meta"]["model_calls"] == 3
    pending = workflow.ask(
        run,
        "question-too-soon",
        "Explain INV-0002",
        model=question_model([{"kind": "invoice", "evidence_id": "R-0002", "focus": "comparison"}]),
    )
    assert pending["question"]["outcome"]["status"] == "rate_limited"
    clock.now += 5
    response = workflow.ask(
        run,
        "question-after-wait",
        "Explain INV-0002",
        model=question_model([{"kind": "invoice", "evidence_id": "R-0002", "focus": "comparison"}]),
    )
    assert response["question"]["outcome"]["status"] == "complete"
    clock.now += 5
    assert workflow.allowance.reserve("remaining-budget", 10)["remaining"] == 45


def test_question_payload_is_bounded_and_bad_json_is_clear():
    http, workflow, _ = setup()
    run = workflow.create()["run_id"]
    large = http.post(
        f"/runs/{run}/questions",
        content=b"x" * 8001,
        headers={**auth(EXECUTE), "Content-Type": "application/json"},
    )
    assert large.status_code == 413
    bad = http.post(
        f"/runs/{run}/questions",
        content=b"{",
        headers={**auth(EXECUTE), "Content-Type": "application/json"},
    )
    assert bad.status_code == 422
    assert workflow.conversation(run)["questions"] == []


def test_current_evidence_view_does_not_replace_canonical_review(monkeypatch):
    import reconciliation.hosted_api as api

    http, workflow, _ = setup()
    run = workflow.create()["run_id"]
    workflow.review(run, "Sample verification")
    before = workflow.read(run)
    original = api.render_report
    monkeypatch.setattr(
        api,
        "render_report",
        lambda facts, meta: original(facts, meta).replace(
            "<body>", "<body><p>Current evidence view</p>"
        ),
    )
    assert http.get(f"/runs/{run}/evidence").status_code == 401
    rendered = http.get(f"/runs/{run}/evidence", headers=auth(READ))
    assert rendered.status_code == 200 and "Current evidence view" in rendered.text
    assert workflow.read(run) == before
    assert http.get(f"/runs/{run}/report", headers=auth(READ)).text == before["report"]


def test_question_limit_gives_a_useful_next_step():
    http, workflow, _ = setup()
    run = workflow.create()["run_id"]
    for n in range(20):
        request = f"limited-question-{n:02d}"
        workflow.conversations.acquire(run, request, "Explain this")
        workflow.conversations.finish(
            run,
            request,
            {"status": "provider_error", "blocks": [], "model_calls": 0, "reported_tokens": None},
        )
    response = http.post(
        f"/runs/{run}/questions",
        json={"request_id": "question-over-limit", "question": "Explain INV-0002"},
        headers=auth(EXECUTE),
    )
    assert response.status_code == 429
    assert "20" in response.json()["detail"]
    assert "new review" in response.json()["detail"].lower()
