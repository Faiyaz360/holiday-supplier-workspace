import pytest
from test_agent import ScriptedModel
from test_cloud_storage import MemoryBucket

from reconciliation.cloud_storage import CloudRunStore
from reconciliation.report import INTRODUCTIONS


def service(supplier="SUP-PMI", store=None):
    from reconciliation.hosted_workflow import HostedWorkflow

    return HostedWorkflow(store or CloudRunStore(MemoryBucket()), supplier)


def model():
    return ScriptedModel(
        responses=[
            {"name": "get_reconciliation", "args": {}},
            {
                "name": "propose_query",
                "args": {
                    "exception_ids": ["R-0002", "R-0003", "R-0004", "R-0100", "R-0101"],
                    "introduction": INTRODUCTIONS[0],
                },
            },
        ]
    )


def test_real_adk_workflow_is_saved_remotely_and_duplicate_request_reuses_it():
    app = service()
    run = app.create()
    first = app.execute(run["run_id"], "request-one", model=model())
    assert first["meta"]["drafting_status"] == "offline_model_verified"
    assert first["reused"] is False
    hostile = ScriptedModel(mode="failure")
    second = app.execute(run["run_id"], "request-one", model=hostile)
    assert second["meta"] == first["meta"]
    assert second["reused"] is True
    assert hostile.cursor == 0
    saved = app.read(run["run_id"])
    assert "EUR 239.00" in saved["meta"]["query_text"]
    assert "EUR 214.00" in saved["report"]


def test_repeat_creation_preserves_existing_cloud_evidence():
    app = service()
    run = app.create()
    done = app.execute(run["run_id"], "request-one", model=model())
    assert app.create() == done["meta"]


def test_another_supplier_cannot_read_review_or_execute_the_run():
    app = service()
    run = app.create()
    other = service("SUP-AYT", app.store)
    with pytest.raises(ValueError, match="scope"):
        other.read(run["run_id"])
    with pytest.raises(ValueError, match="scope"):
        other.review(run["run_id"], "Fictional reviewer")
    hostile = ScriptedModel(mode="failure")
    with pytest.raises(ValueError, match="scope"):
        other.execute(run["run_id"], "request-one", model=hostile)
    assert hostile.cursor == 0


def test_human_review_is_persisted_and_no_further_model_work_starts():
    app = service()
    run = app.create()
    app.execute(run["run_id"], "request-one", model=model())
    reviewed = app.review(run["run_id"], "Fictional reviewer")
    assert reviewed["review"]["reviewer"] == "Fictional reviewer"
    assert reviewed["transmitted"] is False
    hostile = ScriptedModel(mode="failure")
    reused = app.execute(run["run_id"], "request-two", model=hostile)
    assert reused["meta"]["review"] == reviewed["review"]
    assert hostile.cursor == 0


def test_failed_drafting_still_persists_the_factual_report_and_releases_claim():
    app = service()
    run = app.create()
    failed = app.execute(run["run_id"], "request-one", model=ScriptedModel(mode="failure"))
    assert failed["meta"]["drafting_status"] == "provider_error"
    saved = app.read(run["run_id"])
    assert saved["result"]["counts"]["matched"] == 96
    assert "EUR 239.00" in saved["report"]
    assert "query_text" not in saved["meta"]
    retried = app.execute(run["run_id"], "request-two", model=model())
    assert retried["meta"]["drafting_status"] == "offline_model_verified"


def test_simultaneous_execution_requests_start_only_one_model_workflow():
    import asyncio
    import threading
    from concurrent.futures import ThreadPoolExecutor

    class BlockingModel(ScriptedModel):
        started: object
        release: object

        async def generate_content_async(self, llm_request, stream=False):
            self.started.set()
            if not await asyncio.to_thread(self.release.wait, 10):
                raise RuntimeError("Test workflow was not released")
            async for response in super().generate_content_async(llm_request, stream=stream):
                yield response

    app = service()
    run = app.create()
    started, release = threading.Event(), threading.Event()
    slow = BlockingModel(responses=model().responses, started=started, release=release)
    other = service(store=CloudRunStore(app.store.bucket))
    hostile = ScriptedModel(mode="failure")
    with ThreadPoolExecutor(max_workers=1) as workers:
        first = workers.submit(app.execute, run["run_id"], "request-one", model=slow)
        try:
            assert started.wait(10), "The real ADK workflow did not start"
            with pytest.raises(ValueError, match="busy"):
                other.execute(run["run_id"], "request-two", model=hostile)
            assert hostile.cursor == 0
        finally:
            release.set()
        assert first.result(timeout=20)["meta"]["drafting_status"] == "offline_model_verified"
