"""Controlled hosted operations; Google ADK retains its own invocation budgets."""

import asyncio
import hmac
import re
from pathlib import Path
from tempfile import TemporaryDirectory

from .acceptance import assess_saved_run
from .agent_runner import execute_run
from .case_store import CaseInputError, CaseStore
from .cloud_storage import CloudRunStore, RunChangedError, RunFinishedError
from .conversation_store import ConversationStore
from .fixtures import SUPPLIERS
from .model_allowance import AllowanceExceeded, RequestThrottled
from .quality import evaluation_snapshot, recorded_timeline
from .question_agent import answer_question
from .response_workflow import HotelResponses
from .runs import create_run, load_run, review_run
from .source_evidence import resolve_sources
from .statement_import import validate_statement

ROOT = Path(__file__).resolve().parents[1]


class SupplierScopeError(ValueError):
    """The run does not belong to this service's configured supplier."""


class HostedWorkflow:
    """Use synchronous service handlers so blocking storage work stays in a worker thread."""

    def __init__(
        self,
        store: CloudRunStore,
        supplier_id: str,
        data_root: Path | None = None,
        *,
        allowance=None,
    ):
        if supplier_id not in SUPPLIERS:
            raise ValueError("Unknown configured supplier scope")
        self.store = store
        self.conversations = ConversationStore(store.bucket)
        self.case_store = CaseStore(store.bucket)
        self.responses = HotelResponses(self)
        self.allowance = allowance
        self.supplier_id = supplier_id
        self.data_root = Path(data_root) if data_root is not None else ROOT / "data"

    def _scope(self, data: dict):
        if data["result"]["supplier_id"] != self.supplier_id:
            raise SupplierScopeError("Run outside configured supplier scope")

    def create(self, *, review_attempt: str | None = None) -> dict:
        with TemporaryDirectory(prefix="create-hosted-run-") as temporary:
            output = Path(temporary)
            meta = create_run(
                self.data_root / "statements" / f"{self.supplier_id}.csv",
                self.data_root / "ledger.csv",
                output,
                review_attempt=review_attempt,
            )
            try:
                self.store.publish(meta["run_id"], output)
                return meta
            except RunChangedError:
                return self.read(meta["run_id"])["meta"]

    def import_statement(self, raw: bytes, request_id: str, expected_digest: str) -> dict:
        if (
            not isinstance(request_id, str)
            or re.fullmatch(r"[A-Za-z0-9_-]{8,80}", request_id) is None
        ):
            raise ValueError("Invalid import request")
        preview = validate_statement(raw, self.supplier_id, self.data_root / "ledger.csv")
        if not isinstance(expected_digest, str) or not hmac.compare_digest(
            preview["digest"], expected_digest
        ):
            raise ValueError("Statement changed; preview it again before confirming")
        with TemporaryDirectory(prefix="import-statement-") as temporary:
            output = Path(temporary)
            source = output / "statement.csv"
            source.write_bytes(raw)
            meta = create_run(
                source,
                self.data_root / "ledger.csv",
                output,
                review_attempt=request_id,
                preserve_sources=True,
                source_kind="uploaded",
            )
            try:
                self.store.publish(meta["run_id"], output)
                return meta
            except RunChangedError:
                return self.read(meta["run_id"])["meta"]

    def new_review(self, run_id: str, request_id: str) -> dict:
        with TemporaryDirectory(prefix="repeat-statement-") as temporary:
            output = Path(temporary)
            self.store.restore(run_id, output)
            data = load_run(run_id, output)
            self._scope(data)
            statement, ledger = resolve_sources(
                run_id,
                output,
                self.data_root / "statements" / f"{self.supplier_id}.csv",
                self.data_root / "ledger.csv",
            )
            meta = create_run(
                statement,
                ledger,
                output,
                review_attempt=request_id,
                preserve_sources="source_evidence_version" in data["meta"],
                source_kind=data["meta"].get("source_kind", "fixture"),
            )
            if meta["run_id"] == run_id:
                return data["meta"]
            try:
                self.store.publish(meta["run_id"], output)
                return meta
            except RunChangedError:
                return self.read(meta["run_id"])["meta"]

    def current_reference(self) -> str:
        """Find the current fixed statement without creating a stored run."""
        from .cloud_storage import RunNotFoundError

        with TemporaryDirectory(prefix="current-statement-") as temporary:
            meta = create_run(
                self.data_root / "statements" / f"{self.supplier_id}.csv",
                self.data_root / "ledger.csv",
                Path(temporary),
            )
        try:
            self.read(meta["run_id"])
        except RunNotFoundError:
            return ""
        return meta["run_id"]

    def read(self, run_id: str) -> dict:
        data = self.store.read(run_id)
        self._scope(data)
        return data

    def execute(self, run_id: str, request_id: str, *, model=None) -> dict:
        """Only a trusted service handler can supply a model or request execution."""
        with TemporaryDirectory(prefix="execute-hosted-run-") as temporary:
            output = Path(temporary)
            revision = self.store.restore(run_id, output)
            data = load_run(run_id, output)
            self._scope(data)
            if (
                data["meta"].get("review") is not None
                or data["meta"].get("drafting_status") == "live_model_complete"
            ):
                return {
                    "meta": data["meta"],
                    "reused": True,
                    "message": "Existing completed or reviewed run reused",
                }
            statement, ledger = resolve_sources(
                run_id,
                output,
                self.data_root / "statements" / f"{self.supplier_id}.csv",
                self.data_root / "ledger.csv",
            )
            assessment = assess_saved_run(run_id, output, statement, ledger)
            if not all(
                assessment["checks"].get(key) is True
                for key in (
                    "source_files_unchanged",
                    "current_reconciliation_matches",
                )
            ):
                raise ValueError("Source evidence changed; reconcile again before drafting")
            try:
                lease = self.store.acquire(run_id, request_id, expected_generation=revision)
            except RunFinishedError as error:
                return {"meta": self.read(run_id)["meta"], "reused": True, "message": str(error)}
            try:
                reservation = None
                before_calls = data["meta"]["model_calls"]
                if self.allowance is not None:
                    reservation = self.allowance.reserve(
                        f"investigate_{run_id}_{request_id}", 10 - before_calls
                    )
                    if not reservation["granted"]:
                        raise ValueError(
                            "This operation was already reserved; reload its saved outcome"
                        )
                meta = asyncio.run(execute_run(run_id, output, model=model))
                self.store.publish(run_id, output, lease=lease)
                confirmation = self._confirm_usage(reservation, meta["model_calls"] - before_calls)
                return {
                    "meta": meta,
                    "reused": False,
                    "message": "Execution outcome saved",
                    "usage_confirmation": confirmation,
                }
            except BaseException:
                try:
                    self.store.release(lease)
                except ValueError:
                    raise RuntimeError(
                        "The saved outcome is unconfirmed and the execution claim could not be released. Reload before retrying; the claim expires automatically."
                    ) from None
                raise

    def conversation(self, run_id: str) -> dict:
        self.read(run_id)
        return self.conversations.read(run_id)

    def ask(self, run_id: str, request_id: str, question: str, *, model=None) -> dict:
        data = self.read(run_id)
        claim = self.conversations.acquire(run_id, request_id, question)
        if not claim["claimed"]:
            return claim
        reservation = None
        if self.allowance is not None:
            try:
                reservation = self.allowance.reserve(f"question_{run_id}_{request_id}", 4)
                if not reservation["granted"]:
                    raise ValueError("Request was already reserved; reload its outcome")
            except (AllowanceExceeded, RequestThrottled) as error:
                record = self.conversations.finish(
                    run_id,
                    request_id,
                    {
                        "status": "rate_limited"
                        if isinstance(error, RequestThrottled)
                        else "allowance_exhausted",
                        "blocks": [],
                        "model_calls": 0,
                        "reported_tokens": None,
                    },
                )
                return {"claimed": True, "question": record}
        outcome = asyncio.run(answer_question(data, question, model=model))
        record = self.conversations.finish(run_id, request_id, outcome)
        confirmation = self._confirm_usage(reservation, outcome["model_calls"])
        return {"claimed": True, "question": record, "usage_confirmation": confirmation}

    def _confirm_usage(self, reservation: dict | None, calls: int) -> str:
        if reservation is None:
            return "not_required"
        try:
            self.allowance.finish(reservation, calls)
            return "confirmed"
        except ValueError:
            # Outcome is already durable. Retain the larger allowance reservation
            # and expose this state rather than claiming an unverified refund.
            return "reserved"

    def review(self, run_id: str, reviewer: str) -> dict:
        with TemporaryDirectory(prefix="review-hosted-run-") as temporary:
            output = Path(temporary)
            revision = self.store.restore(run_id, output)
            data = load_run(run_id, output)
            self._scope(data)
            if data["meta"]["review"] is not None:
                return data["meta"]
            meta = review_run(run_id, reviewer, output)
            self.store.publish(run_id, output, expected_generation=revision)
            return meta

    def cases(self, run_id):
        data = self.read(run_id)
        board = self.case_store.read(run_id)
        rows = [row for row in data["result"]["rows"] if row["status"] != "matched"]
        return board | {"rows": rows}

    def case_action(self, run_id, **action):
        data = self.read(run_id)
        if not any(
            row["id"] == action["evidence_id"] and row["status"] != "matched"
            for row in data["result"]["rows"]
        ):
            raise CaseInputError("Choose a difference from this statement")
        self.case_store.act(run_id, **action)
        return self.cases(run_id)

    def quality(self, run_id):
        data = self.read(run_id)
        with TemporaryDirectory(prefix="quality-check-") as temporary:
            output = Path(temporary)
            restored_revision = self.store.restore(run_id, output)
            if restored_revision != data["revision"]:
                raise RunChangedError(
                    "Statement changed while checking quality; reload and try again"
                )
            statement, ledger = resolve_sources(
                run_id,
                output,
                self.data_root / "statements" / f"{self.supplier_id}.csv",
                self.data_root / "ledger.csv",
            )
            assessment = assess_saved_run(run_id, output, statement, ledger)
        complete = data["meta"].get("drafting_status") in {
            "live_model_complete",
            "offline_model_verified",
        }
        model_checks = {
            "only_allowed_tools",
            "model_budget",
            "last_tool_budget",
            "required_workflow_tools",
            "factual_query",
        }
        keys = {
            "saved_integrity",
            "source_files_unchanged",
            "current_reconciliation_matches",
            "no_transmission",
        } | model_checks
        checks = {}
        for key in sorted(keys):
            value = assessment["checks"].get(key)
            if value is False:
                checks[key] = "failed"
            elif value is None or (key in model_checks and not complete):
                checks[key] = "not_run"
            else:
                checks[key] = "passed" if value is True else "unavailable"
        meta = data["meta"]
        return {
            "run_id": run_id,
            "current": {
                "checks": checks,
                "execution_kind": meta.get("drafting_status"),
                "model": meta.get("served_model_version") or meta.get("model"),
                "model_calls": meta.get("model_calls", 0),
                "reported_tokens": meta.get("total_tokens"),
                "duration_seconds": meta.get("duration_seconds"),
                "source_kind": meta.get("source_kind", "fixture"),
            },
            "timeline": recorded_timeline(data),
            "historical": evaluation_snapshot(),
        }
