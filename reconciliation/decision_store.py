"""Atomic comparison and conservative shared trial-budget records."""

import copy
import re
import time

from .cloud_records import read_record, write_record
from .cloud_storage import RunBusyError, RunChangedError
from .decision_policy import build_task
from .decision_provider import number

MAX_BYTES = 2 * 1024 * 1024


def valid_id(value):
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_-]{8,120}", value) is None:
        raise ValueError("Invalid comparison identifier")


def record_key(run_id):
    if not isinstance(run_id, str) or re.fullmatch(r"[a-f0-9]{20}", run_id) is None:
        raise ValueError("Invalid statement reference")
    return f"decision-comparisons/{run_id}.json"


class DecisionStore:
    def __init__(self, bucket, *, clock=time.time):
        self.bucket, self.clock = bucket, clock

    def _read(self, run_id):
        data, generation = read_record(self.bucket, record_key(run_id), MAX_BYTES)
        if data is None:
            data = {"schema_version": 1, "run_id": run_id, "comparisons": []}
        if (
            set(data) != {"schema_version", "run_id", "comparisons"}
            or data["schema_version"] != 1
            or data["run_id"] != run_id
            or not isinstance(data["comparisons"], list)
            or len(data["comparisons"]) > 20
        ):
            raise ValueError("Invalid saved comparison")
        seen = set()
        for r in data["comparisons"]:
            valid_id(r["request_id"])
            if (
                r["request_id"] in seen
                or r["status"] not in {"pending", "finished"}
                or not isinstance(r["results"], dict)
                or set(r["results"]) - {"rules", "jev", "d1_ultra", "gemini"}
            ):
                raise ValueError("Invalid saved comparison record")
            number(r["started_at"])
            number(r["expires_at"])
            if r["expires_at"] != r["started_at"] + 150:
                raise ValueError("Invalid comparison expiry")
            seen.add(r["request_id"])
        return data, generation

    def read(self, run_id):
        data, _ = self._read(run_id)
        for r in data["comparisons"]:
            if r["status"] == "pending" and r["expires_at"] <= self.clock():
                r["status"] = "uncertain"
        return data

    def acquire(self, run_id, request_id, task_digest, case_id):
        valid_id(request_id)
        if not isinstance(task_digest, str) or re.fullmatch(r"[a-f0-9]{64}", task_digest) is None:
            raise ValueError("Invalid task fingerprint")
        for _ in range(4):
            data, generation = self._read(run_id)
            for r in data["comparisons"]:
                if r["request_id"] == request_id:
                    if r["task_digest"] != task_digest or r["case_id"] != case_id:
                        raise ValueError("Comparison identifier belongs to another case")
                    return {
                        "claimed": False,
                        "comparison": next(
                            x
                            for x in self.read(run_id)["comparisons"]
                            if x["request_id"] == request_id
                        ),
                    }
            if any(
                r["status"] == "pending" and r["expires_at"] > self.clock()
                for r in data["comparisons"]
            ):
                raise RunBusyError("A comparison is already running")
            if len(data["comparisons"]) >= 20:
                raise ValueError("This statement has twenty comparisons")
            now = self.clock()
            r = {
                "request_id": request_id,
                "case_id": case_id,
                "task_digest": task_digest,
                "started_at": now,
                "expires_at": now + 150,
                "status": "pending",
                "results": {},
                "order": [],
                "task": build_task(case_id),
            }
            data["comparisons"].append(r)
            try:
                write_record(self.bucket, record_key(run_id), data, generation, MAX_BYTES)
                return {"claimed": True, "comparison": copy.deepcopy(r)}
            except RunChangedError:
                continue
        raise RunChangedError("Comparison changed repeatedly")

    def save_result(self, run_id, request_id, name, result, *, finish=False):
        if name not in {"rules", "jev", "d1_ultra", "gemini"} or not isinstance(result, dict):
            raise ValueError("Invalid comparison result")
        for _ in range(4):
            data, generation = self._read(run_id)
            r = next((x for x in data["comparisons"] if x["request_id"] == request_id), None)
            if r is None or r["status"] != "pending" or r["expires_at"] <= self.clock():
                raise ValueError("Comparison outcome is unconfirmed; reload saved results")
            if name in r["results"]:
                if r["results"][name] != result:
                    raise ValueError("Saved result cannot be replaced")
            else:
                r["results"][name] = copy.deepcopy(result)
                r["order"].append(name)
            if finish:
                r["status"] = "finished"
            try:
                write_record(self.bucket, record_key(run_id), data, generation, MAX_BYTES)
                return copy.deepcopy(r)
            except RunChangedError:
                continue
        raise RunChangedError("Comparison changed repeatedly")

    def finish(self, run_id, request_id):
        for _ in range(4):
            data, generation = self._read(run_id)
            r = next((x for x in data["comparisons"] if x["request_id"] == request_id), None)
            if r is None:
                raise ValueError("Comparison is unavailable")
            if r["status"] == "finished":
                return copy.deepcopy(r)
            r["status"] = "finished"
            try:
                write_record(self.bucket, record_key(run_id), data, generation, MAX_BYTES)
                return copy.deepcopy(r)
            except RunChangedError:
                continue
        raise RunChangedError("Comparison completion is unconfirmed")


class DecisionBudget:
    KEY = "decision-lab/initial-trial-budget.json"

    def __init__(self, bucket, *, seed=None):
        self.bucket = bucket
        self.seed = copy.deepcopy(seed)

    def _read(self):
        d, g = read_record(self.bucket, self.KEY, 65536)
        if d is None and self.seed is not None:
            d = copy.deepcopy(self.seed)
        if d is None:
            d = {"schema_version": 1, "limit_usd": 1.0, "stopped": False, "reservations": {}}
        if (
            set(d) != {"schema_version", "limit_usd", "stopped", "reservations"}
            or d["schema_version"] != 1
            or d["limit_usd"] != 1.0
            or type(d["stopped"]) is not bool
            or not isinstance(d["reservations"], dict)
            or len(d["reservations"]) > 40
        ):
            raise ValueError("Invalid trial budget")
        for k, r in d["reservations"].items():
            valid_id(k)
            number(r["reserved_usd"], 0, 1)
            if r["confirmed_usd"] is not None:
                number(r["confirmed_usd"], 0, 10)
        return d, g

    def read(self):
        d, _ = self._read()
        return {
            "limit_usd": 1.0,
            "reserved_usd": round(sum(r["reserved_usd"] for r in d["reservations"].values()), 6),
            "known_spend_usd": sum(r["confirmed_usd"] or 0 for r in d["reservations"].values()),
            "requests": len(d["reservations"]),
            "stopped": d["stopped"],
        }

    def reserve(self, request_id):
        valid_id(request_id)
        for _ in range(4):
            d, g = self._read()
            if request_id in d["reservations"]:
                raise ValueError("Provider request already reserved; never retry automatically")
            if (
                d["stopped"]
                or len(d["reservations"]) >= 40
                or sum(r["reserved_usd"] for r in d["reservations"].values()) + 0.02 > 1
            ):
                raise ValueError(
                    "Initial trial allowance is paused or exhausted; saved comparisons remain available"
                )
            d["reservations"][request_id] = {"reserved_usd": 0.02, "confirmed_usd": None}
            try:
                write_record(self.bucket, self.KEY, d, g, 65536)
                return
            except RunChangedError:
                continue
        raise RunChangedError("Trial allowance changed repeatedly")

    def settle(self, request_id, result):
        for _ in range(4):
            d, g = self._read()
            r = d["reservations"].get(request_id)
            if r is None:
                raise ValueError("Missing trial reservation")
            cost = result.get("cost", {})
            usd = cost.get("usd")
            confirmed = cost.get("kind") in {"reported", "not_called"} and usd is not None
            if confirmed:
                number(usd, 0, 10)
                r["confirmed_usd"] = usd
                r["reserved_usd"] = usd
            if not confirmed or usd > 0.02 or result.get("http_status") in {401, 402, 429}:
                d["stopped"] = True
            try:
                write_record(self.bucket, self.KEY, d, g, 65536)
                return
            except RunChangedError:
                continue
        raise RunChangedError("Trial allowance outcome is unconfirmed")
