"""Bounded durable hotel replies; original text and analysis stay outside financial bundles."""

import copy
import hashlib
import json
import math
import re
import time

from .cloud_records import read_record, write_record
from .cloud_storage import RunBusyError, RunChangedError

MAX_BYTES = 1_500_000
OUTCOMES = {
    "complete",
    "provider_error",
    "invalid_answer",
    "timeout",
    "call_limit",
    "rate_limited",
    "allowance_exhausted",
}


class ReplyInputError(ValueError):
    """A hotel reply or proposal is not safe to use."""


def reply_text(value):
    if (
        not isinstance(value, str)
        or not 12 <= len(value) <= 8000
        or not value.strip()
        or any((ord(c) < 32 and c not in "\n\r\t") or ord(c) == 127 for c in value)
    ):
        raise ReplyInputError(
            "Use 12 to 8,000 characters of UTF-8 reply text; no hidden control characters"
        )
    return value


def _key(run_id):
    if not isinstance(run_id, str) or re.fullmatch(r"[a-f0-9]{20}", run_id) is None:
        raise ReplyInputError("Invalid statement reference")
    return f"hotel-replies/{run_id}.json"


def _id(value):
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_-]{8,80}", value) is None:
        raise ReplyInputError("Invalid reply request")


def _outcome(value):
    if (
        not isinstance(value, dict)
        or value.get("status") not in OUTCOMES
        or type(value.get("model_calls")) is not int
        or not 0 <= value["model_calls"] <= 4
        or not isinstance(value.get("proposals"), list)
        or len(value["proposals"]) > 5
    ):
        raise ReplyInputError("Invalid saved reply analysis")
    if value.get("reported_tokens") is not None and (
        type(value["reported_tokens"]) is not int or value["reported_tokens"] < 0
    ):
        raise ReplyInputError("Invalid reply usage")
    if (value["status"] == "complete") != bool(value["proposals"]):
        raise ReplyInputError("Unfinished analysis cannot propose an action")
    for p in value["proposals"]:
        if (
            not isinstance(p, dict)
            or set(p)
            != {
                "evidence_id",
                "invoice_ref",
                "quote",
                "next_action",
                "suggested_status",
                "summary",
                "next_step",
            }
            or any(not isinstance(v, str) or not v or len(v) > 18000 for v in p.values())
            or p["suggested_status"] not in {"checking", "waiting"}
        ):
            raise ReplyInputError("Invalid saved reply proposal")
    if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode()) > 100000:
        raise ReplyInputError("Reply analysis exceeds its saved limit")


class ReplyStore:
    def __init__(self, bucket, *, clock=time.time):
        self.bucket, self.clock = bucket, clock

    def _read(self, run_id):
        value, generation = read_record(self.bucket, _key(run_id), MAX_BYTES)
        if value is None:
            value = {"schema_version": 1, "run_id": run_id, "replies": []}
        if (
            set(value) != {"schema_version", "run_id", "replies"}
            or type(value["schema_version"]) is not int
            or value["schema_version"] != 1
            or value["run_id"] != run_id
            or not isinstance(value["replies"], list)
            or len(value["replies"]) > 20
        ):
            raise ReplyInputError("Saved replies are unavailable")
        seen = set()
        for r in value["replies"]:
            if not isinstance(r, dict) or set(r) != {
                "request_id",
                "filename",
                "text",
                "sha256",
                "source_digest",
                "started_at",
                "expires_at",
                "status",
                "outcome",
            }:
                raise ReplyInputError("Invalid saved reply")
            _id(r["request_id"])
            reply_text(r["text"])
            if (
                r["request_id"] in seen
                or r["status"] not in {"pending", "uncertain", "finished"}
                or r["sha256"] != hashlib.sha256(r["text"].encode()).hexdigest()
                or not isinstance(r["source_digest"], str)
                or re.fullmatch(r"[a-f0-9]{64}", r["source_digest"]) is None
                or not isinstance(r["filename"], str)
                or not 1 <= len(r["filename"]) <= 120
                or any(ord(c) < 32 or ord(c) == 127 for c in r["filename"])
            ):
                raise ReplyInputError("Reply source failed validation")
            if (
                any(
                    type(r[k]) not in {int, float} or not math.isfinite(r[k])
                    for k in ["started_at", "expires_at"]
                )
                or r["expires_at"] != r["started_at"] + 120
            ):
                raise ReplyInputError("Invalid reply deadline")
            seen.add(r["request_id"])
            if r["status"] == "finished":
                _outcome(r["outcome"])
            elif r["outcome"] is not None:
                raise ReplyInputError("Invalid pending reply")
        return value, generation

    def read(self, run_id):
        value, _ = self._read(run_id)
        for r in value["replies"]:
            if r["status"] == "pending" and r["expires_at"] <= self.clock():
                r["status"] = "uncertain"
        return value

    def acquire(self, run_id, request_id, text, filename, source_digest):
        _id(request_id)
        reply_text(text)
        if (
            not isinstance(filename, str)
            or not 1 <= len(filename) <= 120
            or any(ord(c) < 32 or ord(c) == 127 for c in filename)
            or not isinstance(source_digest, str)
            or re.fullmatch(r"[a-f0-9]{64}", source_digest) is None
        ):
            raise ReplyInputError("Use a short filename and a valid statement source")
        for _ in range(4):
            value, generation = self._read(run_id)
            for r in value["replies"]:
                if r["request_id"] == request_id:
                    if (r["text"], r["filename"], r["source_digest"]) != (
                        text,
                        filename,
                        source_digest,
                    ):
                        raise ReplyInputError("Reply request belongs to a different source")
                    return {"claimed": False, "reply": self._display(r)}
            if any(
                r["status"] == "pending" and r["expires_at"] > self.clock()
                for r in value["replies"]
            ):
                raise RunBusyError("A reply is already being analysed")
            if len(value["replies"]) >= 20:
                raise ReplyInputError(
                    "This statement has twenty saved replies; open a new review to continue"
                )
            now = self.clock()
            record = {
                "request_id": request_id,
                "filename": filename,
                "text": text,
                "sha256": hashlib.sha256(text.encode()).hexdigest(),
                "source_digest": source_digest,
                "started_at": now,
                "expires_at": now + 120,
                "status": "pending",
                "outcome": None,
            }
            value["replies"].append(record)
            try:
                write_record(self.bucket, _key(run_id), value, generation, MAX_BYTES)
                return {"claimed": True, "reply": copy.deepcopy(record)}
            except RunChangedError:
                continue
        raise RunChangedError("Replies changed repeatedly; reload before continuing")

    def _display(self, record):
        r = copy.deepcopy(record)
        if r["status"] == "pending" and r["expires_at"] <= self.clock():
            r["status"] = "uncertain"
        return r

    def finish(self, run_id, request_id, outcome):
        _outcome(outcome)
        for _ in range(4):
            value, generation = self._read(run_id)
            record = next((r for r in value["replies"] if r["request_id"] == request_id), None)
            if record is None:
                raise ReplyInputError("Reply is unavailable")
            if record["status"] == "finished" and record["outcome"] == outcome:
                return record
            if record["status"] != "pending" or record["expires_at"] <= self.clock():
                raise ReplyInputError(
                    "Reply outcome is unconfirmed; inspect the saved source before another request"
                )
            record["status"] = "finished"
            record["outcome"] = copy.deepcopy(outcome)
            try:
                write_record(self.bucket, _key(run_id), value, generation, MAX_BYTES)
                return record
            except RunChangedError:
                continue
        raise RunChangedError("Reply changed repeatedly; reload its outcome")
