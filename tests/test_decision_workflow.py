from test_cloud_storage import MemoryBucket

from reconciliation.cloud_storage import CloudRunStore
from reconciliation.decision_policy import rules_decisions
from reconciliation.decision_workflow import DecisionLab
from reconciliation.hosted_workflow import HostedWorkflow


def test_comparison_is_idempotent_scoped_and_preserves_finance():
    w = HostedWorkflow(CloudRunStore(MemoryBucket()), "SUP-PMI")
    ref = w.create()["run_id"]
    before = w.read(ref)
    calls = []

    def provider(task, model):
        calls.append(model)
        r = rules_decisions(task)
        r.update(
            requested_model=model,
            returned_model=model,
            cost={"usd": 0.0001, "kind": "reported", "source": "Test"},
        )
        return r

    lab = DecisionLab(
        w,
        include_recordings=False,
        provider_runner=provider,
        gemini_runner=lambda t: provider(t, "gemini-test"),
    )
    r = lab.compare(ref, "compare-first", "credit")
    assert r["claimed"] and len(calls) == 3
    assert lab.compare(ref, "compare-first", "credit")["claimed"] is False
    assert len(calls) == 3 and before == w.read(ref)
    assert len(r["comparison"]["results"]) == 4


def test_partial_failure_does_not_rerun_successful_provider():
    w = HostedWorkflow(CloudRunStore(MemoryBucket()), "SUP-PMI")
    ref = w.create()["run_id"]
    calls = []

    def provider(task, model):
        calls.append(model)
        return {
            "status": "timeout",
            "answers": {},
            "cost": {"usd": None, "kind": "unknown"},
            "http_status": None,
        }

    lab = DecisionLab(w, include_recordings=False, provider_runner=provider)
    lab.compare(ref, "compare-first", "credit")
    assert len(calls) == 1
    saved = lab.read(ref)["comparisons"][0]
    assert "rules" in saved["results"]
    lab.compare(ref, "compare-first", "credit")
    assert len(calls) == 1
