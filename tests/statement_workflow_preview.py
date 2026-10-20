"""Separate offline preview for the statement workflow; no production secrets."""

import os

from container_smoke import EXECUTE, READ, REVIEW, OfflineBucket, OfflineModel, OfflineReplyModel
from test_question_agent import model as question_model

from reconciliation.cloud_storage import CloudRunStore
from reconciliation.hosted_api import create_hosted_app
from reconciliation.hosted_workflow import HostedWorkflow
from reconciliation.password_access import PasswordAccess, hash_password


def create_app():
    bucket = OfflineBucket()
    workflow = HostedWorkflow(CloudRunStore(bucket), "SUP-PMI")
    gate = PasswordAccess(
        bucket,
        hash_password("A fictional password for tests only!"),
        "http://localhost:8773",
        allow_local_http=True,
    )
    os.environ["DEMO_API_TOKEN"] = READ
    os.environ["DEMO_LIVE_APPROVED"] = "0"
    os.environ["DEMO_API_BASE_URL"] = "http://localhost:8773"
    return create_hosted_app(
        workflow,
        read_token=READ,
        execution_token=EXECUTE,
        review_token=REVIEW,
        model_factory=OfflineModel,
        reply_model_factory=OfflineReplyModel,
        password_access=gate,
        question_model_factory=lambda: question_model(
            [{"kind": "invoice", "evidence_id": "R-0002", "focus": "comparison"}]
        ),
    )
