import json
import threading

import pytest
from google.api_core.exceptions import PreconditionFailed
from test_runs import make_run

from reconciliation.runs import load_run, review_run


class MemoryBucket:
    """Emulate atomic generation preconditions at the external storage boundary."""

    def __init__(self):
        self.objects = {}
        self.lock = threading.Lock()

    def blob(self, name):
        return MemoryBlob(self, name)

    def get_blob(self, name, **kwargs):
        with self.lock:
            if name not in self.objects:
                return None
            blob = MemoryBlob(self, name)
            blob.generation, data = self.objects[name]
            blob.size = len(data)
            return blob


class MemoryBlob:
    def __init__(self, bucket, name):
        self.bucket, self.name = bucket, name
        self.generation = None

    def upload_from_string(self, data, *, if_generation_match, **kwargs):
        with self.bucket.lock:
            generation = self.bucket.objects.get(self.name, (0, b""))[0]
            if generation != if_generation_match:
                raise PreconditionFailed("Private service details")
            self.generation = generation + 1
            self.bucket.objects[self.name] = (self.generation, data.encode())

    def download_as_bytes(self, *, if_generation_match, **kwargs):
        with self.bucket.lock:
            generation, data = self.bucket.objects[self.name]
            if generation != if_generation_match:
                raise PreconditionFailed("Private service details")
            return data


def store():
    from reconciliation.cloud_storage import CloudRunStore

    return CloudRunStore(MemoryBucket())


def test_saved_run_round_trip_restores_exact_evidence_and_excludes_other_files(tmp_path):
    run = make_run(tmp_path)
    (tmp_path / "outputs/.env").write_text("private-secret-must-not-travel")
    remote = store()
    generation = remote.publish(run["run_id"], tmp_path / "outputs")
    restored_generation = remote.restore(run["run_id"], tmp_path / "restored")
    assert generation == restored_generation == 1
    assert load_run(run["run_id"], tmp_path / "restored") == load_run(
        run["run_id"], tmp_path / "outputs"
    )
    assert len(list((tmp_path / "restored" / run["run_id"]).iterdir())) == 3
    assert "private-secret-must-not-travel" not in repr(remote.bucket.objects)


def test_stale_update_cannot_overwrite_a_newer_review(tmp_path):
    run = make_run(tmp_path)
    remote = store()
    remote.publish(run["run_id"], tmp_path / "outputs")
    revision_a = remote.restore(run["run_id"], tmp_path / "worker-a")
    revision_b = remote.restore(run["run_id"], tmp_path / "worker-b")
    review_run(run["run_id"], "Fictional reviewer", tmp_path / "worker-b")
    assert remote.publish(run["run_id"], tmp_path / "worker-b", expected_generation=revision_b) == 2
    with pytest.raises(ValueError, match="changed"):
        remote.publish(run["run_id"], tmp_path / "worker-a", expected_generation=revision_a)
    remote.restore(run["run_id"], tmp_path / "latest")
    assert (
        load_run(run["run_id"], tmp_path / "latest")["meta"]["review"]["reviewer"]
        == "Fictional reviewer"
    )


def test_default_create_cannot_replace_existing_cloud_or_local_evidence(tmp_path):
    run = make_run(tmp_path)
    remote = store()
    remote.publish(run["run_id"], tmp_path / "outputs")
    with pytest.raises(ValueError, match="changed"):
        remote.publish(run["run_id"], tmp_path / "outputs")
    with pytest.raises(ValueError, match="already"):
        remote.restore(run["run_id"], tmp_path / "outputs")


@pytest.mark.parametrize("change", ["tamper", "extra_file"])
def test_invalid_cloud_bundle_is_rejected_before_restoring_run_files(tmp_path, change):
    run = make_run(tmp_path)
    remote = store()
    remote.publish(run["run_id"], tmp_path / "outputs")
    name = next(iter(remote.bucket.objects))
    generation, raw = remote.bucket.objects[name]
    bundle = json.loads(raw)
    if change == "tamper":
        bundle["files"]["report.html"] += "changed"
    else:
        bundle["files"]["../private.txt"] = "Must not escape the destination"
    remote.bucket.objects[name] = (generation, json.dumps(bundle).encode())
    with pytest.raises(ValueError):
        remote.restore(run["run_id"], tmp_path / "restored")
    assert not (tmp_path / "restored" / run["run_id"]).exists()
    assert not (tmp_path / "private.txt").exists()


def test_storage_preserves_verified_windows_line_endings_exactly(tmp_path):
    import hashlib

    from reconciliation.runs import run_folder

    run = make_run(tmp_path)
    folder = run_folder(run["run_id"], tmp_path / "outputs")
    report = (folder / "report.html").read_bytes().replace(b"\n", b"\r\n")
    (folder / "report.html").write_bytes(report)
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    meta["report_sha256"] = hashlib.sha256(report).hexdigest()
    (folder / "meta.json").write_bytes(json.dumps(meta).encode())
    assert (
        load_run(run["run_id"], tmp_path / "outputs")["meta"]["report_sha256"]
        == meta["report_sha256"]
    )
    remote = store()
    remote.publish(run["run_id"], tmp_path / "outputs")
    remote.restore(run["run_id"], tmp_path / "restored")
    assert (tmp_path / "restored" / run["run_id"] / "report.html").read_bytes() == report
