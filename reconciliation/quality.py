"""Evidence-derived quality summaries and safe recorded action descriptions."""

import json
from pathlib import Path

SNAPSHOT = Path(__file__).parent / "assets" / "evaluation-history.json"
METRICS = {"tool_trajectory_avg_score", "response_match_score"}
TOOL_LABELS = {
    "get_reconciliation": "Compared statement and ledger",
    "get_exception": "Read invoice evidence",
    "lookup_payable": "Requested a finance-service lookup",
    "propose_query": "Proposed a supplier query",
}


def evaluation_snapshot(path=None):
    try:
        raw = (path or SNAPSHOT).read_bytes()
        if len(raw) > 150_000:
            raise ValueError("Snapshot too large")
        value = json.loads(raw)
        if (
            value["schema_version"] != 1
            or len(value["cases"]) != 12
            or len(value["sources"]) != 12
            or len(value["config_sha256"]) != 64
        ):
            raise ValueError("Invalid evaluation snapshot")
        metrics = {}
        ids = set()
        for case in value["cases"]:
            if case["id"] in ids or set(case["metrics"]) != METRICS:
                raise ValueError("Invalid evaluation cases")
            ids.add(case["id"])
            for key in ("prompt", "expected", "actual"):
                if not isinstance(case[key], str) or len(case[key]) > 5000:
                    raise ValueError("Invalid evaluation text")
            for key, item in case["metrics"].items():
                if (
                    item["status"] not in {"PASSED", "FAILED"}
                    or type(item["score"]) not in {int, float}
                    or not 0 <= item["score"] <= 1
                    or item["threshold"] != value["thresholds"][key]
                    or (item["score"] >= item["threshold"]) != (item["status"] == "PASSED")
                ):
                    raise ValueError("Invalid evaluation metric")
                metric = metrics.setdefault(key, {"passed": 0, "total": 0})
                metric["total"] += 1
                metric["passed"] += item["status"] == "PASSED"
        return value | {"available": True, "metrics": metrics}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {
            "available": False,
            "message": "Saved evaluation evidence is unavailable; no score is claimed",
        }


def recorded_timeline(data):
    meta = data["meta"]
    ids = {row["id"]: row["invoice_ref"] for row in data["result"]["rows"]}
    rows = []
    for event in meta.get("events", []):
        kind = event.get("event")
        item = {
            "label": "Recorded workflow event",
            "detail": "No additional supported details are available",
            "state": "recorded",
            "evidence_ids": [],
        }
        if kind == "tool_call":
            item["label"] = TOOL_LABELS.get(event.get("tool"), "Recorded tool request")
            item["detail"] = (
                "A recorded request is not by itself proof that the external check succeeded"
            )
            key = event.get("evidence_id")
            if key in ids:
                item["evidence_ids"] = [key]
                item["detail"] = ids[key]
            elif event.get("invoice_ref") in ids.values():
                item["evidence_ids"] = [
                    key for key, ref in ids.items() if ref == event["invoice_ref"]
                ]
        elif kind == "reconciliation_complete":
            item.update(
                label="Reconciled source records",
                detail="Original statement and ledger figures retained",
            )
        elif kind == "review_started":
            item.update(
                label="Opened a separate review", detail="Earlier saved reports remain unchanged"
            )
        elif kind == "model_attempt":
            item.update(
                label="Requested model response",
                detail="Model request recorded; per-request timing is unavailable",
            )
        elif kind in {"provider_error", "retry_deferred"}:
            item.update(
                label="Model request could not finish",
                detail="Saved source evidence is preserved",
                state="incomplete",
            )
        elif kind == "transient_retry":
            item.update(
                label="Retried a temporary provider failure",
                detail="Retry was recorded within invocation limits",
            )
        elif kind == "drafting_finished":
            done = meta.get("drafting_status") in {"live_model_complete", "offline_model_verified"}
            item.update(
                label="Draft saved for human review"
                if done
                else "Investigation ended without a complete draft",
                detail="See current quality checks for recorded factual validation",
                state="recorded" if done else "incomplete",
            )
        rows.append(item)
    if meta.get("review"):
        rows.append(
            {
                "label": "Human review recorded",
                "detail": "Self-reported reviewer name; no supplier message sent",
                "state": "recorded",
                "evidence_ids": [],
            }
        )
    return rows
