"""Persist complete saved runs using Google Cloud Storage generation preconditions."""

import json
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from google.api_core.exceptions import GoogleAPICallError, PreconditionFailed
from google.auth.exceptions import GoogleAuthError
from requests.exceptions import RequestException

from .runs import load_run, run_folder
from .source_evidence import SOURCE_FILES

RUN_FILES = {"result.json", "meta.json", "report.html"}
MAX_BUNDLE_BYTES = 32_000_000


def _object_name(run_id: str) -> str:
    if not re.fullmatch(r"[a-f0-9]{20}", run_id):
        raise ValueError("Invalid run ID")
    return f"runs/{run_id}.json"


def _validated_files(raw: bytes, run_id: str) -> dict[str, str]:
    if len(raw) > MAX_BUNDLE_BYTES:
        raise ValueError("Stored run exceeds the demo storage limit")
    try:
        bundle = json.loads(raw)
        if (
            type(bundle.get("schema_version")) is not int
            or bundle["schema_version"] not in {1, 2}
            or bundle.get("run_id") != run_id
            or not isinstance(bundle.get("files"), dict)
            or set(bundle["files"])
            != (RUN_FILES if bundle["schema_version"] == 1 else RUN_FILES | SOURCE_FILES)
            or any(not isinstance(value, str) for value in bundle["files"].values())
        ):
            raise ValueError("Invalid stored run")
        with TemporaryDirectory(prefix="validate-cloud-run-") as temporary:
            folder = run_folder(run_id, Path(temporary))
            folder.mkdir()
            for name, content in bundle["files"].items():
                (folder / name).write_bytes(content.encode("utf-8"))
            load_run(run_id, Path(temporary))
        return bundle["files"]
    except (ValueError, TypeError, AttributeError, KeyError, UnicodeError):
        raise ValueError("Stored run unavailable or integrity check failed") from None


class CloudStorageError(ValueError):
    """The storage service or its credentials are unavailable."""


class RunNotFoundError(ValueError):
    """The requested saved run does not exist."""


class RunChangedError(ValueError):
    """The stored revision no longer matches the caller's copy."""


class RunBusyError(ValueError):
    """Another worker currently owns this run."""


class RunFinishedError(ValueError):
    """This request is already resolved, or the run is frozen."""


@dataclass(frozen=True)
class RunLease:
    run_id: str
    request_id: str
    token: str
    generation: int
    expires_at: float


def _request_id(value: str) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{8,80}", value) is not None


class CloudRunStore:
    """Atomic saved runs and execution claims in an injected Google Storage Bucket."""

    def __init__(self, bucket, *, clock=time.time):
        self.bucket = bucket
        self.clock = clock

    def _read(self, run_id: str) -> tuple[dict, int]:
        name = _object_name(run_id)
        try:
            blob = self.bucket.get_blob(name, timeout=10, retry=None)
            if blob is None:
                raise RunNotFoundError("Stored run unavailable")
            if blob.size is None or blob.size > MAX_BUNDLE_BYTES:
                raise ValueError("Stored run exceeds the demo storage limit")
            revision = int(blob.generation)
            raw = blob.download_as_bytes(if_generation_match=revision, timeout=10, retry=None)
        except PreconditionFailed:
            raise RunChangedError("Stored run changed; reload before continuing") from None
        except (GoogleAPICallError, GoogleAuthError, RequestException):
            raise CloudStorageError("Cloud storage unavailable or access denied") from None
        _validated_files(raw, run_id)
        bundle = json.loads(raw)
        requests = bundle.setdefault("requests", {})
        lease = bundle.setdefault("lease", None)
        if (
            not isinstance(requests, dict)
            or len(requests) > 20
            or any(
                not _request_id(key)
                or not isinstance(value, str)
                or value not in {"started", "finished", "aborted"}
                for key, value in requests.items()
            )
        ):
            raise ValueError("Invalid stored execution state")
        if lease is not None:
            if (
                not isinstance(lease, dict)
                or set(lease) != {"request_id", "token", "expires_at"}
                or not _request_id(lease.get("request_id"))
                or not isinstance(lease.get("token"), str)
                or re.fullmatch(r"[a-f0-9]{32}", lease["token"]) is None
                or type(lease.get("expires_at")) not in {int, float}
                or not math.isfinite(lease["expires_at"])
                or requests.get(lease["request_id"]) != "started"
            ):
                raise ValueError("Invalid stored execution claim")
        return bundle, revision

    def _write(self, bundle: dict, expected: int) -> int:
        raw = json.dumps(bundle, ensure_ascii=False)
        _validated_files(raw.encode("utf-8"), bundle["run_id"])
        blob = self.bucket.blob(_object_name(bundle["run_id"]))
        try:
            blob.upload_from_string(
                raw,
                content_type="application/json; charset=utf-8",
                if_generation_match=expected,
                timeout=10,
                retry=None,
            )
            return int(blob.generation)
        except PreconditionFailed:
            raise RunChangedError("Stored run changed; reload before updating") from None
        except (GoogleAPICallError, GoogleAuthError, RequestException):
            raise CloudStorageError("Cloud storage unavailable or access denied") from None

    def _owner(self, bundle: dict, revision: int, lease: RunLease, *, allow_expired=False):
        active = bundle.get("lease")
        if (
            lease.run_id != bundle["run_id"]
            or lease.generation != revision
            or active is None
            or active["token"] != lease.token
            or active["request_id"] != lease.request_id
        ):
            raise ValueError("Execution claim changed; reload before updating")
        if not allow_expired and active["expires_at"] <= self.clock():
            raise ValueError("Execution claim expired; its results cannot replace newer work")

    def acquire(
        self, run_id: str, request_id: str, *, expected_generation: int | None = None
    ) -> RunLease:
        if not _request_id(request_id):
            raise ValueError(
                "Request ID must contain 8 to 80 letters, numbers, dashes or underscores"
            )
        bundle, revision = self._read(run_id)
        if expected_generation is not None and (
            type(expected_generation) is not int or expected_generation != revision
        ):
            raise RunChangedError("Stored run changed; reload before starting execution")
        current = bundle["lease"]
        if current and current["expires_at"] > self.clock():
            raise RunBusyError("Run is busy; an execution is already in progress")
        if request_id in bundle["requests"]:
            raise RunFinishedError("Request already completed or aborted; use its saved outcome")
        meta = json.loads(bundle["files"]["meta.json"])
        if meta.get("review") is not None:
            raise RunFinishedError("Run is reviewed and cannot be redrafted")
        if meta.get("drafting_status") == "live_model_complete":
            raise RunFinishedError("Run already completed; use its saved outcome")
        if type(meta.get("model_calls")) is not int or not 0 <= meta["model_calls"] < 10:
            raise ValueError("Saved model request budget is unavailable or exhausted")
        if len(bundle["requests"]) >= 20:
            raise ValueError("Run operation limit reached; investigate before retrying")
        if current:
            bundle["requests"][current["request_id"]] = "aborted"
        token = uuid4().hex
        expires_at = self.clock() + 180
        bundle["requests"][request_id] = "started"
        bundle["lease"] = {"request_id": request_id, "token": token, "expires_at": expires_at}
        generation = self._write(bundle, revision)
        return RunLease(run_id, request_id, token, generation, expires_at)

    def release(self, lease: RunLease) -> int:
        bundle, revision = self._read(lease.run_id)
        self._owner(bundle, revision, lease, allow_expired=True)
        bundle["requests"][lease.request_id] = "aborted"
        bundle["lease"] = None
        return self._write(bundle, revision)

    def publish(
        self,
        run_id: str,
        output: Path,
        *,
        expected_generation: int = 0,
        lease: RunLease | None = None,
    ) -> int:
        if type(expected_generation) is not int or expected_generation < 0:
            raise ValueError("Expected revision must be a non-negative integer")
        _object_name(run_id)
        if lease is not None:
            if type(lease.generation) is not int or lease.generation <= 0:
                raise ValueError("Invalid execution claim revision")
            if expected_generation not in {0, lease.generation}:
                raise ValueError("Execution claim changed; reload before updating")
            expected_generation = lease.generation
        if expected_generation:
            bundle, revision = self._read(run_id)
            if revision != expected_generation:
                raise RunChangedError("Stored run changed; reload before updating")
            if lease:
                self._owner(bundle, revision, lease)
                bundle["requests"][lease.request_id] = "finished"
                bundle["lease"] = None
            elif bundle["lease"] and bundle["lease"]["expires_at"] > self.clock():
                raise RunBusyError("Run is busy; wait before recording review")
            elif bundle["lease"]:
                bundle["requests"][bundle["lease"]["request_id"]] = "aborted"
                bundle["lease"] = None
        else:
            bundle = {"schema_version": 1, "run_id": run_id, "lease": None, "requests": {}}
        folder = run_folder(run_id, output)
        saved = load_run(run_id, output)
        source_enabled = "source_evidence_version" in saved["meta"]
        bundle["schema_version"] = 2 if source_enabled else 1
        if (folder / ".run.lock").exists():
            raise RunBusyError("Run is busy; retry after the operation finishes")
        files = {}
        for filename in RUN_FILES | SOURCE_FILES if source_enabled else RUN_FILES:
            source = folder / filename
            if source.stat().st_size > MAX_BUNDLE_BYTES:
                raise ValueError("Run exceeds the demo storage limit")
            files[filename] = source.read_bytes().decode("utf-8")
        bundle["files"] = files
        return self._write(bundle, expected_generation)

    def read(self, run_id: str) -> dict:
        """Return one consistent view, excluding private execution claim tokens."""
        bundle, revision = self._read(run_id)
        lease = bundle["lease"]
        last_state = (
            next(reversed(bundle["requests"].values()), None) if bundle["requests"] else None
        )
        status = "idle"
        if lease:
            status = "running" if lease["expires_at"] > self.clock() else "interrupted"
        elif last_state == "aborted":
            status = "interrupted"
        return {
            "meta": json.loads(bundle["files"]["meta.json"]),
            "result": json.loads(bundle["files"]["result.json"]),
            "report": bundle["files"]["report.html"],
            "revision": revision,
            "execution": {"status": status, "expires_at": lease["expires_at"] if lease else None},
        }

    def restore(self, run_id: str, output: Path) -> int:
        target = run_folder(run_id, output)
        if target.exists():
            raise ValueError("Run already exists locally; choose a fresh working directory")
        bundle, revision = self._read(run_id)
        base = Path(output).resolve()
        base.mkdir(parents=True, exist_ok=True)
        # Stage only fixed, hash-validated files and rename on the same filesystem.
        with TemporaryDirectory(prefix=".restore-", dir=base) as temporary:
            staged = run_folder(run_id, Path(temporary))
            staged.mkdir()
            for filename, content in bundle["files"].items():
                (staged / filename).write_bytes(content.encode("utf-8"))
            load_run(run_id, Path(temporary))
            try:
                staged.rename(target)
            except FileExistsError:
                raise ValueError(
                    "Run already exists locally; choose a fresh working directory"
                ) from None
        return revision
