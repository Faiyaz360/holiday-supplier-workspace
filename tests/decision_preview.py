"""Loopback-only recorded-result preview. Never loads owner secrets."""

import json
import os

from container_smoke import EXECUTE, READ, REVIEW, OfflineBucket, OfflineModel, OfflineReplyModel
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from reconciliation.cloud_storage import CloudRunStore
from reconciliation.decision_workflow import DecisionLab
from reconciliation.hosted_api import create_hosted_app
from reconciliation.hosted_workflow import HostedWorkflow
from reconciliation.password_access import PasswordAccess, hash_password


class OfflineQuestionModel(BaseLlm):
    model: str = "offline-refresh-question-model"
    cursor: int = 0

    async def generate_content_async(self, llm_request, stream=False):
        calls = [
            {"name": "get_invoice", "args": {"invoice_ref": "INV-0002"}},
            {
                "name": "submit_answer",
                "args": {
                    "answer_json": json.dumps(
                        [{"kind": "invoice", "evidence_id": "R-0002", "focus": "comparison"}]
                    )
                },
            },
        ]
        call = calls[min(self.cursor, len(calls) - 1)]
        self.cursor += 1
        yield LlmResponse(
            content=types.Content(
                role="model", parts=[types.Part(function_call=types.FunctionCall(**call))]
            )
        )


def create_app():
    os.environ.pop("OPENROUTER_API_KEY", None)
    os.environ["DEMO_LIVE_APPROVED"] = "0"
    os.environ["DEMO_API_BASE_URL"] = "http://127.0.0.1:8774"
    os.environ["DEMO_API_TOKEN"] = READ
    bucket = OfflineBucket()
    w = HostedWorkflow(CloudRunStore(bucket), "SUP-PMI")
    w.create()
    gate = PasswordAccess(
        bucket,
        hash_password("A fictional password for tests only!"),
        "http://localhost:8774",
        allow_local_http=True,
    )
    lab = DecisionLab(w)
    lab.live_enabled = False
    return create_hosted_app(
        w,
        read_token=READ,
        execution_token=EXECUTE,
        review_token=REVIEW,
        password_access=gate,
        model_factory=OfflineModel,
        question_model_factory=OfflineQuestionModel,
        reply_model_factory=OfflineReplyModel,
        decision_lab=lab,
    )
