"""Narrow tools: no filesystem access, sending, payment or review authority."""

import json
from copy import deepcopy
from urllib.parse import quote, urlsplit

import httpx

from .core import LEDGER_FIELDS
from .report import INTRODUCTIONS, render_query


class FinanceClient:
    def __init__(self, base_url: str, token: str, supplier_id: str, client=None):
        parsed = urlsplit(base_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("Only the local simulated finance service is allowed")
        if not token or not supplier_id:
            raise ValueError("Finance service credentials and supplier scope are required")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.supplier_id = supplier_id
        self.client = client

    def lookup(self, invoice_ref: str) -> dict:
        path = (
            f"/suppliers/{quote(self.supplier_id, safe='')}/payables/{quote(invoice_ref, safe='')}"
        )
        headers = {"Authorization": f"Bearer {self.token}"}
        try:
            if self.client is not None:
                response = self.client.get(path, headers=headers)
            else:
                with httpx.Client(
                    base_url=self.base_url, timeout=5, follow_redirects=False, trust_env=False
                ) as client:
                    response = client.get(path, headers=headers)
            if not 200 <= response.status_code < 300:
                raise ValueError("Finance service request failed")
            data = response.json()
        except (httpx.HTTPError, ValueError):
            raise ValueError("Finance service unavailable or request denied") from None
        if (
            data.get("supplier_id") != self.supplier_id
            or data.get("invoice_ref") != invoice_ref
            or not isinstance(data.get("payables"), list)
        ):
            raise ValueError("Finance response does not match trusted supplier scope")
        if any(
            row.get("supplier_id") != self.supplier_id or row.get("invoice_ref") != invoice_ref
            for row in data["payables"]
        ):
            raise ValueError("Finance response contains out-of-scope records")
        return data


class ReconciliationTools:
    def __init__(self, data: dict, finance: FinanceClient | None = None):
        self.result = deepcopy(data["result"])
        self.finance = finance
        self.proposal = None
        if finance is not None and finance.supplier_id != self.result["supplier_id"]:
            raise ValueError("Finance client supplier differs from run scope")

    def get_reconciliation(self) -> dict:
        """Get trusted supplier scope, discrepancy IDs and approved query introductions."""
        return {
            key: deepcopy(self.result[key])
            for key in ["supplier_id", "statement_id", "cutoff_date", "counts", "totals"]
        } | {
            "exceptions": [
                {key: row[key] for key in ["id", "invoice_ref", "status", "overdue"]}
                for row in self.result["rows"]
                if row["status"] != "matched"
            ],
            "approved_introductions": list(INTRODUCTIONS),
        }

    def get_exception(self, exception_id: str) -> dict:
        """Read the original statement and ledger evidence for one discrepancy ID."""
        for row in self.result["rows"]:
            if row["id"] == exception_id and row["status"] != "matched":
                return deepcopy(row)
        raise ValueError("Unknown exception in this supplier scope")

    def lookup_payable(self, invoice_ref: str) -> dict:
        """Look up an invoice in this run's supplier only. No cross-supplier search."""
        allowed = {row["invoice_ref"] for row in self.result["rows"]}
        if invoice_ref not in allowed:
            raise ValueError("Unknown invoice in this supplier scope")
        rows = [
            deepcopy(line)
            for row in self.result["rows"]
            if row["invoice_ref"] == invoice_ref
            for line in row["ledger_lines"]
        ]
        if self.finance is not None:
            evidence = self.finance.lookup(invoice_ref)

            def snapshot(records):
                return sorted(
                    json.dumps({key: record.get(key) for key in LEDGER_FIELDS}, sort_keys=True)
                    for record in records
                )

            if snapshot(evidence["payables"]) != snapshot(rows):
                raise ValueError("Finance records changed; reconcile again before drafting")
            return evidence
        return {
            "supplier_id": self.result["supplier_id"],
            "invoice_ref": invoice_ref,
            "source": "local_ledger_snapshot",
            "payables": rows,
        }

    def propose_query(self, exception_ids: list[str], introduction: str) -> dict:
        """Prepare a factual query using exception IDs and an approved introduction. Never sends."""
        text = render_query(self.result, exception_ids, introduction)
        self.proposal = {
            "status": "awaiting_human_review",
            "exception_ids": list(exception_ids),
            "text": text,
        }
        return deepcopy(self.proposal)

    def agent_tools(self) -> list:
        return [
            self.get_reconciliation,
            self.get_exception,
            self.lookup_payable,
            self.propose_query,
        ]
