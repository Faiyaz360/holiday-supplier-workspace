"""Check stored workflow contracts without making model calls or changing evidence."""

import hashlib
import re
from pathlib import Path

from .core import reconcile_files
from .report import INTRODUCTIONS, render_query
from .runs import load_run

ALLOWED_TOOLS = {"get_reconciliation", "get_exception", "lookup_payable", "propose_query"}
COMPLETE_STATUSES = {"offline_model_verified", "live_model_complete"}


def assess_saved_run(run_id: str, output: Path, statement_path: Path, ledger_path: Path) -> dict:
    """Validate source facts and code-rendered drafts, not free-form model reasoning."""
    assessment = {
        "evaluation": "saved_run_contract",
        "contract_status": "failed",
        "evidence_mode": "unavailable",
        "checks": {"saved_integrity": False},
        "live_generation": False,
        "limits": "Checks stored facts, exact code-rendered drafts and recorded tool names. Does not grade free-form explanations, certify unrecorded actions or verify cloud operation.",
    }
    checks = assessment["checks"]
    try:
        data = load_run(run_id, output)
    except (OSError, ValueError):
        return assessment
    checks["saved_integrity"] = True
    facts, meta = data["result"], data["meta"]
    status = meta.get("drafting_status")
    complete = status in COMPLETE_STATUSES
    assessment["evidence_mode"] = {
        "offline_model_verified": "offline_integration",
        "live_model_complete": "recorded_live_artifact",
        "offline_template": "template_only",
    }.get(status, "incomplete_agent_run")
    try:
        inputs = {
            "statement": hashlib.sha256(Path(statement_path).read_bytes()).hexdigest(),
            "ledger": hashlib.sha256(Path(ledger_path).read_bytes()).hexdigest(),
        }
        checks["source_files_unchanged"] = inputs == meta.get("input_sha256")
        checks["current_reconciliation_matches"] = (
            reconcile_files(statement_path, ledger_path, demo=meta.get("source_kind") != "uploaded")
            == facts
        )
    except (OSError, ValueError):
        checks["source_files_unchanged"] = False
        checks["current_reconciliation_matches"] = False
    checks["no_transmission"] = meta.get("transmitted") is False
    events = meta.get("events", [])
    events_valid = isinstance(events, list) and all(isinstance(e, dict) for e in events)
    events = events if events_valid else []
    names = [event.get("tool") for event in events if event.get("event") == "tool_call"]
    checks["only_allowed_tools"] = events_valid and all(
        isinstance(name, str) and name in ALLOWED_TOOLS for name in names
    )
    # Tool budgets apply per invocation. Additional authorised reads are allowed.
    previous_finish = max(
        (i for i, e in enumerate(events) if e.get("event") == "drafting_finished"),
        default=-1,
    )
    invocation_events = events[:previous_finish] if previous_finish >= 0 else events
    previous_boundary = max(
        (i for i, e in enumerate(invocation_events) if e.get("event") == "drafting_finished"),
        default=-1,
    )
    invocation_names = [
        e.get("tool")
        for e in invocation_events[previous_boundary + 1 :]
        if e.get("event") == "tool_call"
    ]
    calls = meta.get("model_calls")
    checks["model_budget"] = type(calls) is int and 0 <= calls <= 10
    checks["last_tool_budget"] = len(invocation_names) <= 40
    checks["required_workflow_tools"] = (
        all(isinstance(name, str) for name in invocation_names)
        and {"get_reconciliation", "propose_query"}.issubset(invocation_names)
        if complete
        else None
    )
    checks["factual_query"] = None
    query = meta.get("query_text")
    if query is not None or complete:
        checks["factual_query"] = False
        if isinstance(query, str):
            ids = re.findall(r"^(R-[0-9]+) \|", query, re.MULTILINE)
            expected = {row["id"] for row in facts["rows"] if row["status"] != "matched"}
            try:
                checks["factual_query"] = set(ids) == expected and any(
                    query == render_query(facts, ids, opening) for opening in INTRODUCTIONS
                )
            except ValueError:
                pass
    if any(value is False for value in checks.values()):
        assessment["contract_status"] = "failed"
    elif complete:
        assessment["contract_status"] = "passed"
    else:
        assessment["contract_status"] = "incomplete"
    return assessment
