import hashlib
import json
from pathlib import Path

import pytest
from test_cloud_storage import MemoryBucket
from test_hosted_workflow import model

from reconciliation.cloud_storage import CloudRunStore
from reconciliation.hosted_workflow import HostedWorkflow
from reconciliation.runs import load_run

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "data/statements/SUP-PMI.csv"


def service(bucket=None):
    return HostedWorkflow(CloudRunStore(bucket or MemoryBucket()), "SUP-PMI")


def imported(app, raw=None):
    raw = raw or SAMPLE.read_bytes().replace(b"239.00", b"249.00", 1)
    return app.import_statement(raw, "import-request-one", hashlib.sha256(raw).hexdigest())


def test_v2_bundle_roundtrips_exact_sources(tmp_path):
    app = service()
    run = imported(app)
    generation, raw = app.store.bucket.objects[f"runs/{run['run_id']}.json"]
    bundle = json.loads(raw)
    assert bundle["schema_version"] == 2
    assert len(bundle["files"]) == 5
    app.store.restore(run["run_id"], tmp_path)
    saved = load_run(run["run_id"], tmp_path)
    assert saved["meta"]["source_kind"] == "uploaded"
    assert (
        tmp_path / run["run_id"] / "source-statement.csv"
    ).read_bytes() == SAMPLE.read_bytes().replace(b"239.00", b"249.00", 1)
    assert service(app.store.bucket).read(run["run_id"])["result"] == saved["result"]


def test_import_retry_is_idempotent_and_preserves_review():
    app = service()
    run = imported(app)
    reviewed = app.review(run["run_id"], "Sample verification")
    assert imported(app) == reviewed
    different = SAMPLE.read_bytes().replace(b"239.00", b"259.00", 1)
    second = imported(app, different)
    assert second["run_id"] != run["run_id"]
    assert app.read(run["run_id"])["meta"]["review"] == reviewed["review"]


def test_import_execution_uses_uploaded_sources():
    app = service()
    run = imported(app)
    outcome = app.execute(run["run_id"], "execute-upload-one", model=model())
    assert outcome["meta"]["drafting_status"] == "offline_model_verified"
    assert "EUR 249.00" in outcome["meta"]["query_text"]
    assert "Fictional travel records" not in outcome["meta"]["query_text"]


def test_new_review_preserves_imported_statement():
    app = service()
    run = imported(app)
    app.review(run["run_id"], "Sample verification")
    fresh = app.new_review(run["run_id"], "new-review-upload")
    assert fresh["run_id"] != run["run_id"]
    assert fresh["review"] is None
    assert app.read(fresh["run_id"])["result"] == app.read(run["run_id"])["result"]
    assert app.new_review(run["run_id"], "new-review-upload") == fresh


def test_preview_digest_change_refused():
    app = service()
    with pytest.raises(ValueError, match="preview"):
        app.import_statement(SAMPLE.read_bytes(), "import-request-one", "0" * 64)
    assert app.store.bucket.objects == {}


@pytest.mark.parametrize("change", ["extra_path", "missing_source", "downgrade", "bad_manifest"])
def test_extra_or_invalid_bundle_refused_before_restore(tmp_path, change):
    app = service()
    run = imported(app)
    key = f"runs/{run['run_id']}.json"
    gen, raw = app.store.bucket.objects[key]
    bundle = json.loads(raw)
    if change == "extra_path":
        bundle["files"]["../other"] = "unsafe"
    elif change == "missing_source":
        del bundle["files"]["source-statement.csv"]
    elif change == "downgrade":
        bundle["schema_version"] = 1
    else:
        meta = json.loads(bundle["files"]["meta.json"])
        meta["source_evidence_version"] = False
        bundle["files"]["meta.json"] = json.dumps(meta)
    app.store.bucket.objects[key] = (gen, json.dumps(bundle).encode())
    with pytest.raises(ValueError):
        app.store.restore(run["run_id"], tmp_path)
    assert not (tmp_path / run["run_id"]).exists()
