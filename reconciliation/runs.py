"""Local run storage with safe identifiers and accidental-change detection."""

import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from .core import reconcile_files
from .report import render_report
from .source_evidence import save_sources, validate_sources


def _json(data: dict) -> bytes:
    return (json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def _hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def run_folder(run_id: str, output: Path) -> Path:
    if not re.fullmatch(r"[a-f0-9]{20}", run_id):
        raise ValueError("Invalid run ID")
    base = Path(output).resolve()
    folder = base / run_id
    if folder.is_symlink() or folder.resolve().parent != base:
        raise ValueError("Invalid run directory")
    return folder


def _write_json(path: Path, data: dict):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(_json(data))
    temporary.replace(path)


def save_meta(folder: Path, result: dict, meta: dict):
    html = render_report(result, meta).encode("utf-8")
    temporary = folder / "report.html.tmp"
    temporary.write_bytes(html)
    temporary.replace(folder / "report.html")
    meta["report_sha256"] = _hash(html)
    _write_json(folder / "meta.json", meta)


def load_run(run_id: str, output: Path) -> dict:
    folder = run_folder(run_id, output)
    try:
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        facts = (folder / "result.json").read_bytes()
        report = (folder / "report.html").read_bytes()
        if (
            meta["run_id"] != run_id
            or _hash(facts) != meta["result_sha256"]
            or _hash(report) != meta["report_sha256"]
        ):
            raise ValueError("Run integrity check failed")
        result = json.loads(facts)
        validate_sources(folder, meta)
        if "source_kind" in meta and result.get("demo") is not (meta["source_kind"] == "fixture"):
            raise ValueError("Source provenance integrity check failed")
    except (OSError, KeyError, json.JSONDecodeError):
        raise ValueError("Run unavailable or integrity check failed") from None
    return {"meta": meta, "result": result}


def create_run(
    statement_path: Path,
    ledger_path: Path,
    output: Path,
    *,
    review_attempt: str | None = None,
    preserve_sources: bool = False,
    source_kind: str = "fixture",
) -> dict:
    if review_attempt is not None and (
        not isinstance(review_attempt, str)
        or re.fullmatch(r"[A-Za-z0-9_-]{8,80}", review_attempt) is None
    ):
        raise ValueError("Invalid review request")
    if source_kind not in {"fixture", "uploaded"} or (
        source_kind == "uploaded" and not preserve_sources
    ):
        raise ValueError("Uploaded statements require preserved source evidence")
    started = time.monotonic()
    result = reconcile_files(statement_path, ledger_path, demo=source_kind == "fixture")
    facts = _json(result)
    inputs = {
        "statement": _hash(Path(statement_path).read_bytes()),
        "ledger": _hash(Path(ledger_path).read_bytes()),
    }
    fingerprint = _hash(_json(inputs) + facts + b"schema-v1")
    run_id = (
        fingerprint[:20]
        if review_attempt is None
        else _hash(fingerprint.encode() + b"review-attempt-v1" + review_attempt.encode())[:20]
    )
    folder = run_folder(run_id, output)
    if folder.exists():
        return load_run(run_id, output)["meta"]
    folder.mkdir(parents=True, exist_ok=False)
    (folder / "result.json").write_bytes(facts)
    meta = {
        "run_id": run_id,
        "input_fingerprint": fingerprint,
        "input_sha256": inputs,
        "supplier_id": result["supplier_id"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "drafting_status": "offline_template",
        "review": None,
        "transmitted": False,
        "model_calls": 0,
        "total_tokens": None,
        "duration_seconds": round(time.monotonic() - started, 3),
        "result_sha256": _hash(facts),
        "events": [{"event": "reconciliation_complete", "status": "success"}],
    }
    if preserve_sources:
        save_sources(folder, Path(statement_path).read_bytes(), Path(ledger_path).read_bytes())
        meta["source_evidence_version"] = 1
        meta["source_kind"] = source_kind
    if review_attempt is not None:
        meta["review_attempt"] = review_attempt
        meta["events"].append({"event": "review_started"})
    save_meta(folder, result, meta)
    return meta


def review_run(run_id: str, reviewer: str, output: Path) -> dict:
    reviewer = reviewer.strip()
    if not reviewer or len(reviewer) > 254 or any(ord(c) < 32 for c in reviewer):
        raise ValueError("Reviewer must contain 1 to 254 printable characters")
    folder = run_folder(run_id, output)
    lock = folder / ".run.lock"
    acquired = False
    try:
        with lock.open("x", encoding="utf-8"):
            acquired = True
            data = load_run(run_id, output)
            meta = data["meta"]
            if meta["review"] is None:
                meta["review"] = {
                    "reviewer": reviewer,
                    "at": datetime.now(timezone.utc).isoformat(),
                    "action": "reviewed_locally",
                    "report_sha256": meta["report_sha256"],
                }
                meta["events"].append({"event": "human_review", "status": "reviewed_locally"})
                save_meta(folder, data["result"], meta)
            return meta
    except FileExistsError:
        raise ValueError("Run is busy; retry review") from None
    finally:
        if acquired:
            lock.unlink(missing_ok=True)
