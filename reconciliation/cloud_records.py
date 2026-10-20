"""Small bounded JSON records using the same private bucket and atomic writes."""

import json

from google.api_core.exceptions import GoogleAPICallError, PreconditionFailed
from google.auth.exceptions import GoogleAuthError
from requests.exceptions import RequestException

from .cloud_storage import CloudStorageError, RunChangedError


def read_record(bucket, name: str, maximum: int) -> tuple[dict | None, int]:
    try:
        blob = bucket.get_blob(name, timeout=10, retry=None)
        if blob is None:
            return None, 0
        if blob.size is None or blob.size > maximum:
            raise ValueError("Saved workspace record exceeds its limit")
        generation = int(blob.generation)
        raw = blob.download_as_bytes(if_generation_match=generation, timeout=10, retry=None)
    except PreconditionFailed:
        raise RunChangedError("Workspace record changed; reload before continuing") from None
    except (GoogleAPICallError, GoogleAuthError, RequestException):
        raise CloudStorageError("Workspace storage unavailable") from None
    try:
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("Invalid saved workspace record")
        return value, generation
    except (UnicodeError, ValueError):
        raise ValueError("Saved workspace record failed validation") from None


def write_record(bucket, name: str, value: dict, generation: int, maximum: int) -> None:
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False)
    if len(raw.encode("utf-8")) > maximum:
        raise ValueError("Workspace record size limit reached")
    try:
        bucket.blob(name).upload_from_string(
            raw,
            content_type="application/json; charset=utf-8",
            if_generation_match=generation,
            timeout=10,
            retry=None,
        )
    except PreconditionFailed:
        raise RunChangedError("Workspace record changed; retry safely") from None
    except (GoogleAPICallError, GoogleAuthError, RequestException):
        raise CloudStorageError("Workspace storage unavailable") from None
