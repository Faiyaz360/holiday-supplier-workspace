import pytest
from test_cloud_storage import MemoryBucket

from reconciliation.cloud_storage import RunBusyError
from reconciliation.reply_store import ReplyInputError, ReplyStore

RUN = "a" * 20
TEXT = "The hotel confirms INV-0002 needs a EUR 25 credit check."


def outcome():
    return {
        "status": "complete",
        "proposals": [
            {
                "evidence_id": "R-0002",
                "invoice_ref": "INV-0002",
                "quote": TEXT,
                "next_action": "check_credit",
                "suggested_status": "checking",
                "summary": "Saved evidence",
                "next_step": "Check credit record",
            }
        ],
        "model_calls": 2,
        "reported_tokens": 10,
    }


def test_original_text_digest_and_repeated_claim_are_durable():
    bucket = MemoryBucket()
    s = ReplyStore(bucket, clock=lambda: 100)
    assert s.acquire(RUN, "reply-first", TEXT, "hotel.txt", "f" * 64)["claimed"]
    assert not s.acquire(RUN, "reply-first", TEXT, "hotel.txt", "f" * 64)["claimed"]
    with pytest.raises(ReplyInputError):
        s.acquire(RUN, "reply-first", TEXT + "x", "hotel.txt", "f" * 64)
    record = s.finish(RUN, "reply-first", outcome())
    assert record["text"] == TEXT and record["outcome"]["status"] == "complete"
    assert ReplyStore(bucket).read(RUN)["replies"][0]["text"] == TEXT
    with pytest.raises(ReplyInputError):
        s.finish(RUN, "reply-first", outcome() | {"model_calls": 3})


def test_pending_expiry_never_restarts_or_spends_again():
    now = [100]
    s = ReplyStore(MemoryBucket(), clock=lambda: now[0])
    s.acquire(RUN, "reply-first", TEXT, "hotel.txt", "f" * 64)
    with pytest.raises(RunBusyError):
        s.acquire(RUN, "reply-second", TEXT, "hotel.txt", "f" * 64)
    now[0] = 221
    assert s.read(RUN)["replies"][0]["status"] == "uncertain"
    assert not s.acquire(RUN, "reply-first", TEXT, "hotel.txt", "f" * 64)["claimed"]
    with pytest.raises(ReplyInputError):
        s.finish(RUN, "reply-first", outcome())


@pytest.mark.parametrize("text", ["", "x" * 8001, "bad\x00text", "bad\x7ftext"])
def test_invalid_original_rejected_before_storage(text):
    bucket = MemoryBucket()
    s = ReplyStore(bucket)
    with pytest.raises(ReplyInputError):
        s.acquire(RUN, "reply-first", text, "hotel.txt", "f" * 64)
    assert not bucket.objects


def test_invalid_outcome_or_changed_source_is_rejected():
    bucket = MemoryBucket()
    s = ReplyStore(bucket)
    s.acquire(RUN, "reply-first", TEXT, "hotel.txt", "f" * 64)
    with pytest.raises(ReplyInputError):
        s.finish(RUN, "reply-first", outcome() | {"model_calls": 99})
    with pytest.raises(ReplyInputError):
        s.acquire(RUN, "reply-first", TEXT, "hotel.txt", "e" * 64)


def test_tampered_original_is_rejected():
    import json

    bucket = MemoryBucket()
    s = ReplyStore(bucket)
    s.acquire(RUN, "reply-first", TEXT, "hotel.txt", "f" * 64)
    key = f"hotel-replies/{RUN}.json"
    generation, raw = bucket.objects[key]
    value = json.loads(raw)
    value["replies"][0]["text"] = "Changed INV-0002 source text"
    bucket.objects[key] = (generation, json.dumps(value).encode())
    with pytest.raises(ReplyInputError):
        s.read(RUN)
