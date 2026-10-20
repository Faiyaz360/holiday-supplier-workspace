import pytest
from test_cloud_storage import MemoryBucket
from test_hosted_workflow import service

from reconciliation.case_store import CaseStore
from reconciliation.cloud_storage import RunChangedError

RUN = "a" * 20
PAYLOAD = dict(
    request_id="case-request-01",
    evidence_id="R-0002",
    status="waiting",
    actor="Finance reviewer",
    note="Asked the hotel to check the balance",
)


def test_read_defaults_and_changes_are_append_only_and_idempotent():
    bucket = MemoryBucket()
    store = CaseStore(bucket)
    assert store.read(RUN)["history"] == [] and not bucket.objects
    first = store.act(RUN, expected_generation=0, **PAYLOAD)
    assert (
        first["cases"]["R-0002"]["status"] == "waiting"
        and first["history"][0]["from_status"] == "checking"
    )
    assert store.act(RUN, expected_generation=0, **PAYLOAD) == first
    with pytest.raises(ValueError):
        store.act(
            RUN,
            expected_generation=first["generation"],
            **(PAYLOAD | {"note": "different payload"}),
        )
    second = store.act(
        RUN,
        expected_generation=first["generation"],
        **(
            PAYLOAD
            | {
                "request_id": "case-request-02",
                "status": "resolved",
                "note": "Hotel confirmed the agreed balance",
            }
        ),
    )
    third = store.act(
        RUN,
        expected_generation=second["generation"],
        **(
            PAYLOAD
            | {
                "request_id": "case-request-03",
                "status": "checking",
                "note": "New information needs checking",
            }
        ),
    )
    assert len(third["history"]) == 3 and third["history"][:2] == second["history"]
    assert third["cases"]["R-0002"]["status"] == "checking"
    with pytest.raises(RunChangedError):
        store.act(RUN, expected_generation=0, **(PAYLOAD | {"request_id": "case-stale-01"}))
    assert CaseStore(bucket).read(RUN) == third


@pytest.mark.parametrize(
    "change",
    [
        {"actor": ""},
        {"note": " "},
        {"note": "x" * 501},
        {"status": "paid"},
        {"evidence_id": "../../bad"},
        {"expected_generation": True},
        {"request_id": "tiny"},
        {"actor": "bad\nname"},
    ],
)
def test_invalid_actions_never_write(change):
    bucket = MemoryBucket()
    store = CaseStore(bucket)
    with pytest.raises(ValueError):
        store.act(RUN, **(PAYLOAD | {"expected_generation": 0} | change))
    assert not bucket.objects


def test_follow_up_never_changes_financial_evidence_and_new_review_is_separate():
    app = service()
    run = app.create()
    ref = run["run_id"]
    before = app.read(ref)
    board = app.cases(ref)
    assert len(board["rows"]) == 5
    app.case_action(ref, expected_generation=0, **PAYLOAD)
    assert app.read(ref) == before
    with pytest.raises(ValueError):
        app.case_action(
            ref,
            **(
                PAYLOAD
                | {
                    "expected_generation": 1,
                    "request_id": "matched-case-01",
                    "evidence_id": "R-0001",
                }
            ),
        )
    fresh = app.new_review(ref, "case-new-review-01")
    assert app.cases(fresh["run_id"])["history"] == []


def test_history_limit_and_corruption_do_not_overwrite_saved_actions():
    from reconciliation.case_store import name
    from reconciliation.cloud_records import write_record

    bucket = MemoryBucket()
    store = CaseStore(bucket)
    history = []
    for index in range(500):
        history.append(
            PAYLOAD
            | {
                "request_id": f"history-{index:05}",
                "created_at": "2026-10-19T12:00:00+00:00",
                "from_status": "checking" if index == 0 else "waiting",
            }
        )
    write_record(
        bucket, name(RUN), {"schema_version": 1, "run_id": RUN, "history": history}, 0, 2_000_000
    )
    before = dict(bucket.objects)
    with pytest.raises(ValueError, match="500-action"):
        store.act(RUN, expected_generation=1, **(PAYLOAD | {"request_id": "one-too-many"}))
    assert bucket.objects == before
    history[1]["request_id"] = history[0]["request_id"]
    write_record(
        bucket, name(RUN), {"schema_version": 1, "run_id": RUN, "history": history}, 1, 2_000_000
    )
    with pytest.raises(ValueError):
        store.read(RUN)


def test_simultaneous_saves_have_one_winner():
    from concurrent.futures import ThreadPoolExecutor

    bucket = MemoryBucket()

    def save(index):
        try:
            CaseStore(bucket).act(
                RUN, expected_generation=0, **(PAYLOAD | {"request_id": f"parallel-{index:04}"})
            )
            return True
        except RunChangedError:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(save, range(8)))
    assert sum(outcomes) == 1 and len(CaseStore(bucket).read(RUN)["history"]) == 1


def test_follow_up_can_continue_after_review_without_mutating_the_review():
    app = service()
    ref = app.create()["run_id"]
    app.review(ref, "Review verifier")
    before = app.read(ref)
    app.case_action(ref, expected_generation=0, **PAYLOAD)
    assert app.read(ref) == before
