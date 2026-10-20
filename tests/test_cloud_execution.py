import pytest
from test_cloud_storage import MemoryBucket
from test_runs import make_run

from reconciliation.cloud_storage import CloudRunStore
from reconciliation.runs import load_run, review_run


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def setup(tmp_path):
    clock = Clock()
    run = make_run(tmp_path)
    bucket = MemoryBucket()
    store = CloudRunStore(bucket, clock=clock)
    store.publish(run["run_id"], tmp_path / "outputs")
    return store, run, clock


def test_two_service_instances_cannot_claim_the_same_run(tmp_path):
    store, run, clock = setup(tmp_path)
    store.acquire(run["run_id"], "request-one")
    second = CloudRunStore(store.bucket, clock=clock)
    with pytest.raises(ValueError, match="busy"):
        second.acquire(run["run_id"], "request-two")
    with pytest.raises(ValueError, match="busy"):
        second.acquire(run["run_id"], "request-one")


def test_a_finished_request_cannot_start_generation_again(tmp_path):
    store, run, _ = setup(tmp_path)
    lease = store.acquire(run["run_id"], "request-one")
    store.publish(run["run_id"], tmp_path / "outputs", lease=lease)
    with pytest.raises(ValueError, match="completed"):
        store.acquire(run["run_id"], "request-one")
    assert load_run(run["run_id"], tmp_path / "outputs")["meta"]["model_calls"] == 0


def test_expired_worker_cannot_overwrite_its_replacement(tmp_path):
    store, run, clock = setup(tmp_path)
    old = store.acquire(run["run_id"], "request-one")
    clock.now += 181
    current = store.acquire(run["run_id"], "request-two")
    with pytest.raises(ValueError, match="changed|expired"):
        store.publish(run["run_id"], tmp_path / "outputs", lease=old)
    store.publish(run["run_id"], tmp_path / "outputs", lease=current)
    store.restore(run["run_id"], tmp_path / "restored")
    assert load_run(run["run_id"], tmp_path / "restored")["meta"]["review"] is None


def test_review_cannot_commit_while_an_execution_claim_is_active(tmp_path):
    store, run, _ = setup(tmp_path)
    lease = store.acquire(run["run_id"], "request-one")
    generation = store.restore(run["run_id"], tmp_path / "reviewer")
    review_run(run["run_id"], "Fictional reviewer", tmp_path / "reviewer")
    with pytest.raises(ValueError, match="busy"):
        store.publish(run["run_id"], tmp_path / "reviewer", expected_generation=generation)
    store.release(lease)
    generation = store.restore(run["run_id"], tmp_path / "fresh-reviewer")
    review_run(run["run_id"], "Fictional reviewer", tmp_path / "fresh-reviewer")
    store.publish(run["run_id"], tmp_path / "fresh-reviewer", expected_generation=generation)
    with pytest.raises(ValueError, match="reviewed"):
        store.acquire(run["run_id"], "request-two")


def test_release_preserves_evidence_and_refuses_reusing_an_aborted_request(tmp_path):
    store, run, _ = setup(tmp_path)
    before = load_run(run["run_id"], tmp_path / "outputs")
    lease = store.acquire(run["run_id"], "request-one")
    store.release(lease)
    store.restore(run["run_id"], tmp_path / "restored")
    assert load_run(run["run_id"], tmp_path / "restored") == before
    with pytest.raises(ValueError, match="completed|aborted"):
        store.acquire(run["run_id"], "request-one")


def test_an_expired_claim_cannot_commit_even_without_a_replacement(tmp_path):
    store, run, clock = setup(tmp_path)
    lease = store.acquire(run["run_id"], "request-one")
    clock.now += 181
    with pytest.raises(ValueError, match="expired"):
        store.publish(run["run_id"], tmp_path / "outputs", lease=lease)


def test_progress_is_readable_without_exposing_the_execution_claim_token(tmp_path):
    import json

    store, run, clock = setup(tmp_path)
    lease = store.acquire(run["run_id"], "request-one")
    snapshot = store.read(run["run_id"])
    assert snapshot["execution"]["status"] == "running"
    assert lease.token not in json.dumps(snapshot)
    assert "request-one" not in json.dumps(snapshot)
    clock.now += 181
    assert store.read(run["run_id"])["execution"]["status"] == "interrupted"
