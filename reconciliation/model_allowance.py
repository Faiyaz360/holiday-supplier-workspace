"""Atomic daily model-request reservations; this is not a monetary cutoff."""

import math
import re
import time
from datetime import datetime, timezone

from .cloud_records import read_record, write_record
from .cloud_storage import RunChangedError

MAXIMUM_BYTES = 65536


class AllowanceExceeded(ValueError):
    """No new model work fits within the configured daily request allowance."""


class RequestThrottled(ValueError):
    """The shared workspace must wait briefly before another model operation."""


def _name(day: str) -> str:
    if not isinstance(day, str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) is None:
        raise ValueError("Invalid allowance date")
    return f"model-allowance/{day}.json"


def _id(value):
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_-]{8,128}", value) is None:
        raise ValueError("Invalid model operation identifier")


class ModelAllowance:
    def __init__(self, bucket, *, clock=time.time, daily_limit: int = 60):
        if type(daily_limit) is not int or not 1 <= daily_limit <= 60:
            raise ValueError("Daily model request limit must be between 1 and 60")
        self.bucket, self.clock, self.daily_limit = bucket, clock, daily_limit

    def _read(self, day):
        value, generation = read_record(self.bucket, _name(day), MAXIMUM_BYTES)
        if value is None:
            value = {"schema_version": 1, "day": day, "last_started": None, "reservations": {}}
        if (
            type(value.get("schema_version")) is not int
            or value["schema_version"] != 1
            or value.get("day") != day
            or not isinstance(value.get("reservations"), dict)
            or len(value["reservations"]) > 120
        ):
            raise ValueError("Invalid saved model allowance")
        last = value.get("last_started")
        if last is not None and (type(last) not in {int, float} or not math.isfinite(last)):
            raise ValueError("Invalid saved request throttle")
        for key, item in value["reservations"].items():
            _id(key)
            if (
                not isinstance(item, dict)
                or set(item) != {"max_calls", "actual_calls"}
                or type(item["max_calls"]) is not int
                or not 1 <= item["max_calls"] <= 10
                or (
                    item["actual_calls"] is not None
                    and (
                        type(item["actual_calls"]) is not int
                        or not 0 <= item["actual_calls"] <= item["max_calls"]
                    )
                )
            ):
                raise ValueError("Invalid saved model reservation")
        return value, generation

    def _claim_throttle(self, request_id: str, day: str) -> None:
        # This record is shared across UTC days; the daily allowance is not.
        name = "model-allowance/throttle.json"
        value, generation = read_record(self.bucket, name, 1024)
        if value is not None:
            if (
                set(value) != {"schema_version", "request_id", "day", "last_started"}
                or type(value["schema_version"]) is not int
                or value["schema_version"] != 1
                or type(value["last_started"]) not in {int, float}
                or not math.isfinite(value["last_started"])
            ):
                raise ValueError("Invalid saved workspace throttle")
            _id(value["request_id"])
            _name(value["day"])
            if value["request_id"] == request_id and value["day"] == day:
                return
            if self.clock() - value["last_started"] < 5:
                raise RequestThrottled("Wait five seconds between assistant requests")
        write_record(
            self.bucket,
            name,
            {
                "schema_version": 1,
                "request_id": request_id,
                "day": day,
                "last_started": self.clock(),
            },
            generation,
            1024,
        )

    def reserve(self, request_id: str, max_calls: int) -> dict:
        _id(request_id)
        if type(max_calls) is not int or not 1 <= max_calls <= 10:
            raise ValueError("Invalid model request reservation")
        day = datetime.fromtimestamp(self.clock(), timezone.utc).date().isoformat()
        for _ in range(4):
            try:
                value, generation = self._read(day)
                used = sum(
                    item["max_calls"] if item["actual_calls"] is None else item["actual_calls"]
                    for item in value["reservations"].values()
                )
                previous = value["reservations"].get(request_id)
                handle = {"day": day, "request_id": request_id, "max_calls": max_calls}
                if previous is not None:
                    if previous["max_calls"] != max_calls:
                        raise ValueError("Model reservation changed")
                    return handle | {"granted": False, "remaining": max(0, self.daily_limit - used)}
                if used + max_calls > self.daily_limit or len(value["reservations"]) >= 120:
                    raise AllowanceExceeded(
                        "Workspace daily model allowance reached; try again tomorrow"
                    )
                self._claim_throttle(request_id, day)
                value["last_started"] = self.clock()
                value["reservations"][request_id] = {"max_calls": max_calls, "actual_calls": None}
                write_record(self.bucket, _name(day), value, generation, MAXIMUM_BYTES)
                return handle | {"granted": True, "remaining": self.daily_limit - used - max_calls}
            except RunChangedError:
                continue
        raise RunChangedError("Model allowance changed repeatedly; retry later")

    def finish(self, reservation: dict, actual_calls: int) -> None:
        day = reservation.get("day")
        request_id = reservation.get("request_id")
        _name(day)
        _id(request_id)
        for _ in range(4):
            try:
                value, generation = self._read(day)
                item = value["reservations"].get(request_id)
                if (
                    item is None
                    or item["max_calls"] != reservation.get("max_calls")
                    or type(actual_calls) is not int
                    or not 0 <= actual_calls <= item["max_calls"]
                ):
                    raise ValueError("Invalid model usage confirmation")
                if item["actual_calls"] is not None:
                    if item["actual_calls"] != actual_calls:
                        raise ValueError("Model usage already confirmed differently")
                    return
                item["actual_calls"] = actual_calls
                write_record(self.bucket, _name(day), value, generation, MAXIMUM_BYTES)
                return
            except RunChangedError:
                continue
        raise RunChangedError("Model allowance changed repeatedly; usage remains reserved")
