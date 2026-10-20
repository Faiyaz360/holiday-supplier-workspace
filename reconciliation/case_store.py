"""Human follow-up, stored separately from immutable financial evidence."""

import re
from datetime import datetime, timezone

from .cloud_records import read_record, write_record
from .cloud_storage import RunChangedError

MAX_BYTES = 2_000_000
MAX_ACTIONS = 500
STATUSES = {"checking", "waiting", "resolved"}


class CaseInputError(ValueError):
    """A requested action does not refer to an editable statement difference."""


def text(value, maximum):
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > maximum
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
    ):
        raise ValueError("Enter a name and an explanatory note within the displayed limits")
    return value.strip()


def name(run_id):
    if not isinstance(run_id, str) or re.fullmatch(r"[a-f0-9]{20}", run_id) is None:
        raise ValueError("Invalid statement reference")
    return f"cases/{run_id}.json"


def validate_action(item):
    if not isinstance(item, dict) or set(item) != {
        "request_id",
        "evidence_id",
        "status",
        "actor",
        "note",
        "from_status",
        "created_at",
    }:
        raise ValueError("Invalid saved follow-up action")
    if (
        not isinstance(item["request_id"], str)
        or re.fullmatch(r"[A-Za-z0-9_-]{8,80}", item["request_id"]) is None
    ):
        raise ValueError("Invalid follow-up request")
    if (
        not isinstance(item["evidence_id"], str)
        or re.fullmatch(r"R-\d{4,8}", item["evidence_id"]) is None
    ):
        raise ValueError("Invalid invoice evidence reference")
    if (
        not isinstance(item["status"], str)
        or not isinstance(item["from_status"], str)
        or item["status"] not in STATUSES
        or item["from_status"] not in STATUSES
    ):
        raise ValueError("Choose an available follow-up status")
    text(item["actor"], 254)
    text(item["note"], 500)
    if (
        not isinstance(item["created_at"], str)
        or len(item["created_at"]) > 40
        or datetime.fromisoformat(item["created_at"]).utcoffset() != timezone.utc.utcoffset(None)
    ):
        raise ValueError("Invalid follow-up time")


class CaseStore:
    def __init__(self, bucket):
        self.bucket = bucket

    def read(self, run_id):
        value, generation = read_record(self.bucket, name(run_id), MAX_BYTES)
        if value is None:
            value = {"schema_version": 1, "run_id": run_id, "history": []}
        if (
            set(value) != {"schema_version", "run_id", "history"}
            or type(value["schema_version"]) is not int
            or value["schema_version"] != 1
            or value["run_id"] != run_id
            or not isinstance(value["history"], list)
            or len(value["history"]) > MAX_ACTIONS
        ):
            raise ValueError("Saved follow-up record is unavailable")
        cases, requests = {}, set()
        for action in value["history"]:
            validate_action(action)
            if action["request_id"] in requests or action["from_status"] != cases.get(
                action["evidence_id"], {}
            ).get("status", "checking"):
                raise ValueError("Saved follow-up history failed validation")
            requests.add(action["request_id"])
            cases[action["evidence_id"]] = action
        return value | {
            "generation": generation,
            "cases": cases,
            "can_save": len(value["history"]) < MAX_ACTIONS,
        }

    def act(self, run_id, *, request_id, evidence_id, status, actor, note, expected_generation):
        if type(expected_generation) is not int or expected_generation < 0:
            raise ValueError("Reload this statement before saving follow-up")
        payload = {
            "request_id": request_id,
            "evidence_id": evidence_id,
            "status": status,
            "actor": text(actor, 254),
            "note": text(note, 500),
        }
        action = payload | {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "from_status": "checking",
        }
        validate_action(action)
        board = self.read(run_id)
        previous = next(
            (entry for entry in board["history"] if entry["request_id"] == request_id), None
        )
        if previous is not None:
            if any(previous[key] != value for key, value in payload.items()):
                raise ValueError("Follow-up request already saved with different details")
            return board
        if board["generation"] != expected_generation:
            raise RunChangedError(
                "Follow-up changed. Reload and check the latest history before saving"
            )
        if not board["can_save"]:
            raise ValueError(
                "This statement has reached its 500-action follow-up limit; existing history is preserved"
            )
        action["from_status"] = board["cases"].get(evidence_id, {}).get("status", "checking")
        value = {key: board[key] for key in ("schema_version", "run_id", "history")}
        value["history"] = [*board["history"], action]
        write_record(self.bucket, name(run_id), value, board["generation"], MAX_BYTES)
        return self.read(run_id)
