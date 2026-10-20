"""Optional experiments, isolated from financial evidence and human case decisions."""

import base64
import hashlib
import json
from pathlib import Path

from .decision_evaluation import comparison_summary
from .decision_policy import build_task, case_catalogue, case_labels, rules_decisions
from .decision_provider import run_decision, run_gemini_baseline
from .decision_store import DecisionBudget, DecisionStore


class DecisionLab:
    def __init__(
        self,
        workflow,
        *,
        provider_runner=run_decision,
        gemini_runner=run_gemini_baseline,
        include_recordings=True,
    ):
        self.workflow = workflow
        self.store = DecisionStore(workflow.store.bucket)
        assets = Path(__file__).parent / "assets"
        self.recordings = (
            json.loads((assets / "decision-recordings.json").read_text(encoding="utf-8"))
            if include_recordings
            else {"comparisons": []}
        )
        seed = (
            json.loads((assets / "decision-budget-seed.json").read_text(encoding="utf-8"))
            if include_recordings
            else None
        )
        self.budget = DecisionBudget(workflow.store.bucket, seed=seed)
        self.provider_runner = provider_runner
        self.gemini_runner = gemini_runner

    def read(self, run_id):
        self.workflow.read(run_id)
        saved = self.store.read(run_id)
        labels = {
            c["id"]: case_labels(c["id"])["expected"]
            for c in case_catalogue()
            if c["mode"] == "text"
        }
        combined = self.recordings["comparisons"] + saved["comparisons"]
        matched = [
            r
            for r in combined
            if r.get("task", {}).get("mode") == "text"
            and all(
                r["results"].get(name, {}).get("status") == "complete"
                for name in ("jev", "d1_ultra", "gemini")
            )
        ]
        return saved | {
            "matched_cases": [r["case_id"] for r in matched],
            "matched_summary": comparison_summary(matched, labels),
            "recordings": self.recordings["comparisons"],
            "recording_source": self.recordings.get("source", "No bundled trial"),
            "cases": case_catalogue(),
            "budget": self.budget.read(),
            "summary": comparison_summary(combined, labels),
            "labels": labels,
            "label_status": "author_draft",
            "fixture_image": "data:image/png;base64,"
            + base64.b64encode(
                (Path(__file__).parent / "assets/decision-fixtures/credit-note.png").read_bytes()
            ).decode(),
        }

    def compare(self, run_id, request_id, case_id, mode="text"):
        self.workflow.read(run_id)
        task = build_task(case_id)
        if task["mode"] != mode:
            raise ValueError("Choose the correct case mode")
        digest = hashlib.sha256(
            json.dumps(task, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        claim = self.store.acquire(run_id, request_id, digest, case_id)
        if not claim["claimed"]:
            return claim
        count = len(self.store.read(run_id)["comparisons"])
        order = [("jev", "typesafe/jev-1.14"), ("d1_ultra", "liquid/d1-ultra")]
        if count % 2 == 0:
            order.reverse()
        if mode == "text":
            order.append(("gemini", None))
        rule = (
            rules_decisions(task)
            if mode == "text"
            else {
                "status": "not_supported",
                "answers": {},
                "cost": {"usd": 0, "kind": "not_called", "source": "Text rules only"},
                "elapsed_ms": 0,
            }
        )
        record = self.store.save_result(run_id, request_id, "rules", rule)
        operation = "decision_" + hashlib.sha256(f"{run_id}:{request_id}".encode()).hexdigest()[:40]
        reservation = None
        if self.workflow.allowance is not None:
            reservation = self.workflow.allowance.reserve(operation, len(order))
            if not reservation["granted"]:
                raise ValueError("Comparison already reserved")
        calls = 0
        for index, (name, model) in enumerate(order):
            token = hashlib.sha256(f"{operation}:{name}".encode()).hexdigest()
            if model is not None:
                self.budget.reserve(token)
            calls += 1
            result = self.provider_runner(task, model) if model else self.gemini_runner(task)
            record = self.store.save_result(
                run_id, request_id, name, result, finish=index == len(order) - 1
            )
            if model is not None:
                self.budget.settle(token, result)
                if self.budget.read()["stopped"]:
                    record = self.store.finish(run_id, request_id)
                    break
        if reservation is not None:
            self.workflow._confirm_usage(reservation, calls)
        return {"claimed": True, "comparison": record}
