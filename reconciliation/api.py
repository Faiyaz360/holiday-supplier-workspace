"""A simulated finance service. Read access and human review have separate credentials."""

import hmac
import os
from datetime import date
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .core import load_ledger
from .runs import load_run, review_run, run_folder

ROOT = Path(__file__).resolve().parents[1]


class ReviewRequest(BaseModel):
    reviewer: str = Field(min_length=1, max_length=80)


def create_app(output: Path | None = None, data_root: Path | None = None) -> FastAPI:
    output = Path(output) if output is not None else ROOT / "outputs"
    data_root = Path(data_root) if data_root is not None else ROOT / "data"
    read_token = os.getenv("DEMO_API_TOKEN", "")
    review_token = os.getenv("DEMO_REVIEW_TOKEN", "")
    supplier = os.getenv("DEMO_SUPPLIER_ID", "")
    app = FastAPI(
        title="Fictional holiday supplier finance service",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    def authenticate(authorization: str | None, review: bool = False):
        if len(read_token) < 16 or not supplier:
            raise HTTPException(
                503, "Demo service credentials or supplier scope are not configured"
            )
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(401, "Bearer token required")
        provided = authorization[7:]
        expected = review_token if review else read_token
        if review and (len(review_token) < 16 or review_token == read_token):
            raise HTTPException(503, "Separate reviewer credential is not configured")
        if not hmac.compare_digest(provided.encode(), expected.encode()):
            raise HTTPException(403, "Access denied")

    def scoped_run(run_id: str):
        try:
            data = load_run(run_id, output)
        except ValueError:
            raise HTTPException(404, "Run unavailable") from None
        if data["result"]["supplier_id"] != supplier:
            raise HTTPException(403, "Supplier outside configured scope")
        return data

    @app.get("/health")
    def health():
        return {"status": "ok", "data": "synthetic"}

    @app.get("/suppliers/{supplier_id}/payables/{invoice_ref}")
    def payable(
        supplier_id: str, invoice_ref: str, authorization: str | None = Header(default=None)
    ):
        authenticate(authorization)
        if supplier_id != supplier:
            raise HTTPException(403, "Supplier outside configured scope")
        try:
            rows = load_ledger(data_root / "ledger.csv", date(2026, 10, 18))
        except (OSError, ValueError):
            raise HTTPException(503, "Demo ledger unavailable or invalid") from None
        payables = [
            row
            for row in rows
            if row["supplier_id"] == supplier and row["invoice_ref"] == invoice_ref
        ]
        return {
            "supplier_id": supplier,
            "invoice_ref": invoice_ref,
            "source": "simulated_finance_api",
            "payables": payables,
        }

    @app.get("/runs/{run_id}")
    def get_run(run_id: str, authorization: str | None = Header(default=None)):
        authenticate(authorization)
        return scoped_run(run_id)

    @app.get("/runs/{run_id}/report", response_class=HTMLResponse)
    def report(run_id: str, authorization: str | None = Header(default=None)):
        authenticate(authorization)
        scoped_run(run_id)
        return HTMLResponse(
            (run_folder(run_id, output) / "report.html").read_text(encoding="utf-8"),
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    @app.post("/runs/{run_id}/review")
    def review(run_id: str, body: ReviewRequest, authorization: str | None = Header(default=None)):
        authenticate(authorization, review=True)
        scoped_run(run_id)
        try:
            return review_run(run_id, body.reviewer, output)
        except ValueError:
            raise HTTPException(
                409, "Review could not be recorded; check run integrity or retry"
            ) from None

    return app
