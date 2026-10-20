import asyncio
import json
from pathlib import Path

import pytest
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import errors, types
from test_runs import make_run

from reconciliation.agent_runner import execute_run
from reconciliation.report import INTRODUCTIONS
from reconciliation.runs import load_run


class ScriptedModel(BaseLlm):
    model: str = "offline-test-model"
    responses: list[dict] = []
    cursor: int = 0
    mode: str = "normal"

    async def generate_content_async(self, llm_request, stream=False):
        self.cursor += 1
        if self.mode == "failure":
            raise RuntimeError("private-provider-error-must-not-be-saved")
        if self.mode == "transient":
            raise errors.ServerError(503, {"error": {"message": "private-provider-error"}})
        if self.mode == "timeout":
            await asyncio.sleep(5)
        if self.mode == "loop":
            call = {"name": "get_reconciliation", "args": {}}
        elif self.cursor <= len(self.responses):
            call = self.responses[self.cursor - 1]
        else:
            yield LlmResponse(
                content=types.Content(
                    role="model", parts=[types.Part(text="The factual query is ready for review.")]
                ),
                usage_metadata=types.GenerateContentResponseUsageMetadata(total_token_count=9),
            )
            return
        yield LlmResponse(
            content=types.Content(
                role="model", parts=[types.Part(function_call=types.FunctionCall(**call))]
            ),
            usage_metadata=types.GenerateContentResponseUsageMetadata(total_token_count=7),
        )


def test_real_adk_runner_executes_tools_with_offline_model(tmp_path):
    run = make_run(tmp_path)
    model = ScriptedModel(
        responses=[
            {"name": "get_reconciliation", "args": {}},
            {"name": "get_exception", "args": {"exception_id": "R-0002"}},
            {
                "name": "propose_query",
                "args": {
                    "exception_ids": ["R-0002", "R-0003", "R-0004", "R-0100", "R-0101"],
                    "introduction": INTRODUCTIONS[0],
                },
            },
        ]
    )
    meta = asyncio.run(execute_run(run["run_id"], tmp_path / "outputs", model=model))
    assert meta["drafting_status"] == "offline_model_verified"
    assert meta["model_calls"] == 4
    assert meta["total_tokens"] == 30
    assert meta["adk_version"]
    assert [event["tool"] for event in meta["events"] if event["event"] == "tool_call"] == [
        "get_reconciliation",
        "get_exception",
        "propose_query",
    ]
    assert "EUR 239.00" in meta["query_text"]
    assert meta["review"] is None


@pytest.mark.parametrize(
    "mode,status,calls",
    [
        ("failure", "provider_error", 1),
        ("transient", "provider_error", 3),
        ("loop", "call_limit", 10),
        ("timeout", "timeout", 1),
    ],
)
def test_failures_are_bounded_and_preserve_reconciliation(tmp_path, mode, status, calls):
    run = make_run(tmp_path)
    meta = asyncio.run(
        execute_run(
            run["run_id"],
            tmp_path / "outputs",
            model=ScriptedModel(mode=mode),
            timeout_seconds=0.05 if mode == "timeout" else 30,
        )
    )
    assert meta["drafting_status"] == status
    if mode == "timeout":
        # An overall deadline may expire before the first provider request.
        assert 0 <= meta["model_calls"] <= calls
    else:
        assert meta["model_calls"] == calls
    saved = load_run(run["run_id"], tmp_path / "outputs")
    assert saved["result"]["counts"]["matched"] == 96
    assert "private-provider-error" not in json.dumps(saved)
    assert (tmp_path / "outputs" / run["run_id"] / "report.html").exists()


def test_missing_live_approval_does_not_contact_a_model(tmp_path, monkeypatch):
    monkeypatch.delenv("DEMO_LIVE_APPROVED", raising=False)
    run = make_run(tmp_path)
    meta = asyncio.run(execute_run(run["run_id"], tmp_path / "outputs"))
    assert meta["drafting_status"] == "live_not_enabled"
    assert meta["model_calls"] == 0


def test_incomplete_or_wrong_draft_falls_back_to_template(tmp_path):
    run = make_run(tmp_path)
    model = ScriptedModel(
        responses=[
            {
                "name": "propose_query",
                "args": {
                    "exception_ids": ["R-0001"],
                    "introduction": "EUR 999.00 has been approved",
                },
            }
        ]
    )
    meta = asyncio.run(execute_run(run["run_id"], tmp_path / "outputs", model=model))
    assert meta["drafting_status"] != "offline_model_verified"
    assert "query_text" not in meta
    assert "999.00" not in json.dumps(load_run(run["run_id"], tmp_path / "outputs"))


def test_evaluation_files_validate_against_installed_adk_schema():
    from google.adk.evaluation.eval_config import EvalConfig
    from google.adk.evaluation.eval_set import EvalSet

    root = Path(__file__).resolve().parents[1]
    evaluations = EvalSet.model_validate_json(
        (root / "evals/reconciliation.evalset.json").read_text()
    )
    assert len(evaluations.eval_cases) >= 10
    assert len({case.eval_id for case in evaluations.eval_cases}) == len(evaluations.eval_cases)
    EvalConfig.model_validate_json((root / "evals/test_config.json").read_text())


def test_provider_details_do_not_leak_to_application_logs(tmp_path, caplog):
    import logging

    run = make_run(tmp_path)
    with caplog.at_level(logging.ERROR):
        asyncio.run(
            execute_run(run["run_id"], tmp_path / "outputs", model=ScriptedModel(mode="failure"))
        )
    assert "private-provider-error-must-not-be-saved" not in caplog.text


def test_reviewed_report_cannot_be_redrafted(tmp_path):
    from reconciliation.runs import review_run

    run = make_run(tmp_path)
    reviewed = review_run(run["run_id"], "Person", tmp_path / "outputs")
    with pytest.raises(ValueError, match="frozen"):
        asyncio.run(
            execute_run(run["run_id"], tmp_path / "outputs", model=ScriptedModel(mode="failure"))
        )
    assert (
        load_run(run["run_id"], tmp_path / "outputs")["meta"]["report_sha256"]
        == reviewed["report_sha256"]
    )


def test_provider_status_code_is_saved_without_private_error_details(tmp_path):
    run = make_run(tmp_path)
    meta = asyncio.run(
        execute_run(run["run_id"], tmp_path / "outputs", model=ScriptedModel(mode="transient"))
    )
    statuses = [
        event["http_status"] for event in meta["events"] if event["event"] == "provider_error"
    ]
    assert statuses == [503, 503, 503]
    assert "private-provider-error" not in json.dumps(meta)


def test_sdk_wrapped_timeout_keeps_the_timeout_status(tmp_path, monkeypatch):
    from google.adk.events import Event
    from google.adk.runners import Runner
    from google.adk.workflow import NodeTimeoutError

    async def timeout_events(self, **kwargs):
        yield Event(author="reconciler")
        raise NodeTimeoutError(node_name="reconciler", timeout=120)

    monkeypatch.setattr(Runner, "run_async", timeout_events)
    run = make_run(tmp_path)
    meta = asyncio.run(execute_run(run["run_id"], tmp_path / "outputs", model=ScriptedModel()))
    assert meta["drafting_status"] == "timeout"
    assert load_run(run["run_id"], tmp_path / "outputs")["result"]["counts"]["matched"] == 96
