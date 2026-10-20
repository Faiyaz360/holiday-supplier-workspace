import pytest
from test_cloud_storage import MemoryBucket

from reconciliation.cloud_storage import RunBusyError
from reconciliation.decision_store import DecisionBudget, DecisionStore

RUN = "a" * 20
DIGEST = "b" * 64


def test_duplicates_changes_partial_results_and_expiry():
    now = [100]
    s = DecisionStore(MemoryBucket(), clock=lambda: now[0])
    assert s.acquire(RUN, "compare-first", DIGEST, "credit")["claimed"]
    assert not s.acquire(RUN, "compare-first", DIGEST, "credit")["claimed"]
    with pytest.raises(ValueError):
        s.acquire(RUN, "compare-first", "c" * 64, "credit")
    with pytest.raises(RunBusyError):
        s.acquire(RUN, "compare-second", DIGEST, "credit")
    s.save_result(RUN, "compare-first", "rules", {"status": "complete", "answers": {}})
    now[0] = 251
    r = s.read(RUN)["comparisons"][0]
    assert r["status"] == "uncertain" and "rules" in r["results"]
    assert not s.acquire(RUN, "compare-first", DIGEST, "credit")["claimed"]


def test_unknown_charge_stops_future_spending_and_reserves_money():
    b = DecisionBudget(MemoryBucket())
    b.reserve("compare-first-jev")
    b.settle("compare-first-jev", {"cost": {"usd": None, "kind": "unknown"}, "http_status": None})
    with pytest.raises(ValueError):
        b.reserve("compare-second-jev")
    assert b.read()["reserved_usd"] >= 0.02


def test_budget_ceiling_survives_multiple_instances():
    bucket = MemoryBucket()
    for i in range(40):
        DecisionBudget(bucket).reserve(f"operation-{i:04}")
    with pytest.raises(ValueError):
        DecisionBudget(bucket).reserve("operation-over-limit")
    assert DecisionBudget(bucket).read()["reserved_usd"] <= 1
