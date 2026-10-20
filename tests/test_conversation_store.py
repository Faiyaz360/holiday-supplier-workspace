import pytest
from test_cloud_execution import Clock
from test_cloud_storage import MemoryBucket

RUN = "a" * 20


def store(bucket=None, clock=None):
    from reconciliation.conversation_store import ConversationStore

    return ConversationStore(bucket or MemoryBucket(), clock=clock or Clock())


def test_questions_are_separate_durable_and_idempotent():
    bucket = MemoryBucket()
    first = store(bucket)
    assert first.read(RUN)["questions"] == []
    claim = first.acquire(RUN, "question-one", "Why is this different?")
    assert claim["claimed"] is True
    repeat = store(bucket).acquire(RUN, "question-one", "Why is this different?")
    assert repeat["claimed"] is False
    assert repeat["question"]["status"] == "pending"
    result = {"status": "complete", "blocks": [], "model_calls": 2, "reported_tokens": 123}
    first.finish(RUN, "question-one", result)
    saved = store(bucket).read(RUN)
    assert saved["questions"][0]["outcome"] == result
    assert all(key.startswith("conversations/") for key in bucket.objects)
    assert first.acquire(RUN, "question-one", "Why is this different?")["claimed"] is False
    with pytest.raises(ValueError, match="different question"):
        first.acquire(RUN, "question-one", "Changed text")


def test_active_question_and_expired_claim_never_start_twice():
    clock = Clock()
    first = store(clock=clock)
    first.acquire(RUN, "question-one", "Explain this")
    with pytest.raises(ValueError, match="progress"):
        first.acquire(RUN, "question-two", "Explain another")
    clock.now += 121
    assert first.read(RUN)["questions"][0]["status"] == "uncertain"
    assert first.acquire(RUN, "question-one", "Explain this")["claimed"] is False
    assert first.acquire(RUN, "question-two", "Explain another")["claimed"] is True
    with pytest.raises(ValueError, match="expired|changed"):
        first.finish(
            RUN,
            "question-one",
            {"status": "complete", "blocks": [], "model_calls": 1, "reported_tokens": 1},
        )


def test_conversation_bounds_and_request_ids():
    first = store()
    for i in range(20):
        first.acquire(RUN, f"question-{i:03d}", "Explain this")
        first.finish(
            RUN,
            f"question-{i:03d}",
            {"status": "provider_error", "blocks": [], "model_calls": 1, "reported_tokens": None},
        )
    with pytest.raises(ValueError, match="20"):
        first.acquire(RUN, "question-extra", "Explain this")
    with pytest.raises(ValueError):
        first.acquire("../escape", "question-one", "Explain this")
    with pytest.raises(ValueError):
        first.acquire(RUN, "../escape", "Explain this")
    with pytest.raises(ValueError):
        store().acquire(RUN, "question-one", "x" * 1001)


def test_unknown_storage_corruption_fails_closed():
    bucket = MemoryBucket()
    first = store(bucket)
    first.acquire(RUN, "question-one", "Explain this")
    key = next(iter(bucket.objects))
    gen, _ = bucket.objects[key]
    bucket.objects[key] = (gen, b"{}")
    with pytest.raises(ValueError):
        first.read(RUN)


def test_concurrent_questions_only_grant_one_execution():
    from concurrent.futures import ThreadPoolExecutor

    bucket = MemoryBucket()

    def attempt(n):
        try:
            return store(bucket).acquire(RUN, f"parallel-question-{n}", "Explain this")["claimed"]
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(8)))
    assert results.count(True) == 1
