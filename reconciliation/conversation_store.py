"""Durable shared questions, separate from immutable invoice review bundles."""

import copy
import json
import math
import re
import time

from .cloud_records import read_record, write_record
from .cloud_storage import RunBusyError, RunChangedError

MAXIMUM_BYTES = 524288


class ConversationLimit(ValueError):
    """A fresh review is needed to keep a shared conversation bounded."""


OUTCOMES = {
    "complete",
    "provider_error",
    "timeout",
    "invalid_answer",
    "call_limit",
    "allowance_exhausted",
    "rate_limited",
}


def _key(run_id: str) -> str:
    if not isinstance(run_id, str) or re.fullmatch(r"[a-f0-9]{20}", run_id) is None:
        raise ValueError("Invalid statement reference")
    return f"conversations/{run_id}.json"


def _question(request_id: str, text: str) -> None:
    if not isinstance(request_id, str) or re.fullmatch(r"[A-Za-z0-9_-]{8,80}", request_id) is None:
        raise ValueError("Invalid question request")
    if (
        not isinstance(text, str)
        or not 1 <= len(text) <= 1000
        or not text.strip()
        or any(ord(c) < 32 or ord(c) == 127 for c in text)
    ):
        raise ValueError("Use a question between 1 and 1,000 printable characters")


def _outcome(value: dict) -> None:
    if (
        not isinstance(value, dict)
        or value.get("status") not in OUTCOMES
        or type(value.get("model_calls")) is not int
        or not 0 <= value["model_calls"] <= 4
        or not isinstance(value.get("blocks"), list)
        or len(value["blocks"]) > 12
        or any(not isinstance(block, dict) for block in value["blocks"])
        or (
            value.get("reported_tokens") is not None
            and (type(value["reported_tokens"]) is not int or value["reported_tokens"] < 0)
        )
        or len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")) > 24000
    ):
        raise ValueError("Invalid saved answer")


class ConversationStore:
    def __init__(self, bucket, *, clock=time.time):
        self.bucket, self.clock = bucket, clock

    def _read(self, run_id):
        value, generation = read_record(self.bucket, _key(run_id), MAXIMUM_BYTES)
        if value is None:
            value = {"schema_version": 1, "run_id": run_id, "questions": []}
        if (
            type(value.get("schema_version")) is not int
            or value["schema_version"] != 1
            or value.get("run_id") != run_id
            or not isinstance(value.get("questions"), list)
            or len(value["questions"]) > 20
        ):
            raise ValueError("Invalid saved conversation")
        seen = set()
        for record in value["questions"]:
            if not isinstance(record, dict) or set(record) != {
                "request_id",
                "question",
                "started_at",
                "expires_at",
                "status",
                "outcome",
            }:
                raise ValueError("Invalid saved question")
            _question(record["request_id"], record["question"])
            if record["request_id"] in seen or record["status"] not in {
                "pending",
                "uncertain",
                "finished",
            }:
                raise ValueError("Invalid saved question state")
            seen.add(record["request_id"])
            if (
                any(
                    type(record[k]) not in {int, float} or not math.isfinite(record[k])
                    for k in ("started_at", "expires_at")
                )
                or record["expires_at"] != record["started_at"] + 120
            ):
                raise ValueError("Invalid saved question deadline")
            if record["status"] == "finished":
                _outcome(record["outcome"])
            elif record["outcome"] is not None:
                raise ValueError("Invalid pending question")
        return value, generation

    def read(self, run_id: str) -> dict:
        value, _ = self._read(run_id)
        for record in value["questions"]:
            if record["status"] == "pending" and record["expires_at"] <= self.clock():
                record["status"] = "uncertain"
        return value

    def acquire(self, run_id: str, request_id: str, question: str) -> dict:
        _question(request_id, question)
        for _ in range(4):
            try:
                value, generation = self._read(run_id)
                for record in value["questions"]:
                    if record["status"] == "pending" and record["expires_at"] <= self.clock():
                        record["status"] = "uncertain"
                    if record["request_id"] == request_id:
                        if record["question"] != question:
                            raise ValueError("Request identifier belongs to a different question")
                        return {"claimed": False, "question": record}
                if any(record["status"] == "pending" for record in value["questions"]):
                    raise RunBusyError("A question is already in progress for this statement")
                if len(value["questions"]) >= 20:
                    raise ConversationLimit("This statement has reached its 20-question limit")
                now = self.clock()
                record = {
                    "request_id": request_id,
                    "question": question,
                    "started_at": now,
                    "expires_at": now + 120,
                    "status": "pending",
                    "outcome": None,
                }
                value["questions"].append(record)
                write_record(self.bucket, _key(run_id), value, generation, MAXIMUM_BYTES)
                return {"claimed": True, "question": copy.deepcopy(record)}
            except RunChangedError:
                continue
        raise RunChangedError("Conversation changed repeatedly; reload before asking again")

    def finish(self, run_id: str, request_id: str, outcome: dict) -> dict:
        _outcome(outcome)
        for _ in range(4):
            try:
                value, generation = self._read(run_id)
                record = next(
                    (item for item in value["questions"] if item["request_id"] == request_id), None
                )
                if record is None:
                    raise ValueError("Question request is unavailable")
                if record["status"] == "finished" and record["outcome"] == outcome:
                    return record
                if record["status"] != "pending" or record["expires_at"] <= self.clock():
                    raise ValueError("Question claim expired or changed; reload its saved outcome")
                record["status"] = "finished"
                record["outcome"] = copy.deepcopy(outcome)
                write_record(self.bucket, _key(run_id), value, generation, MAXIMUM_BYTES)
                return record
            except RunChangedError:
                continue
        raise RunChangedError("Conversation changed repeatedly; reload its saved outcome")
