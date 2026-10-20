"""Isolated loopback preview; never copied into the production image."""

import os
import tempfile
import time
from pathlib import Path

from container_smoke import EXECUTE, READ, REVIEW, OfflineBucket, OfflineModel

from reconciliation.cloud_storage import CloudRunStore
from reconciliation.hosted_api import create_hosted_app
from reconciliation.hosted_workflow import HostedWorkflow
from reconciliation.password_access import PasswordAccess, hash_password

EXPIRY_FLAG = Path(tempfile.gettempdir()) / "holiday-password-expiry-bb5e7b4e.flag"

TEST_PASSWORD = "A fictional password for tests only!"


def create_app():
    bucket = OfflineBucket()
    workflow = HostedWorkflow(CloudRunStore(bucket), "SUP-PMI")
    workflow.create()
    gate = PasswordAccess(
        bucket,
        hash_password(TEST_PASSWORD),
        "http://localhost:8772",
        allow_local_http=True,
        clock=lambda: time.time() + (3601 if EXPIRY_FLAG.exists() else 0),
    )
    os.environ["DEMO_API_TOKEN"] = READ
    os.environ["DEMO_LIVE_APPROVED"] = "0"
    os.environ["DEMO_API_BASE_URL"] = "http://localhost:8772"
    return create_hosted_app(
        workflow,
        read_token=READ,
        execution_token=EXECUTE,
        review_token=REVIEW,
        model_factory=OfflineModel,
        password_access=gate,
    )
