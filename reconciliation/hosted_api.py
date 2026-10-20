"""Authenticated HTTP access to the hosted supplier workflow."""

import hmac
import os
import re
from datetime import date
from typing import Annotated, Literal

from fastapi import FastAPI, Header, HTTPException, Path, Query, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from .case_store import CaseInputError
from .cloud_storage import (
    CloudRunStore,
    CloudStorageError,
    RunBusyError,
    RunChangedError,
    RunNotFoundError,
)
from .conversation_store import ConversationLimit
from .core import load_ledger, load_statement
from .decision_workflow import DecisionLab
from .hosted_workflow import HostedWorkflow, SupplierScopeError
from .identity import IapIdentity, IdentityDenied, IdentityUnavailable
from .model_allowance import AllowanceExceeded, ModelAllowance, RequestThrottled
from .password_access import COOKIE, SESSION_SECONDS, PasswordAccess
from .recovery_examples import recovery_example
from .reply_store import ReplyInputError
from .report import render_query, render_report, render_supplier_email
from .statement_comparison import compare_statements
from .statement_import import MAX_UPLOAD_BYTES, validate_statement
from .workflow_examples import comparison_example

RunId = Annotated[str, Path(pattern=r"^[a-f0-9]{20}$")]


async def read_statement_body(request: Request) -> bytes:
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "text/csv":
        raise HTTPException(415, "Choose a CSV file saved using UTF-8")
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "CSV exceeds the 1 MB upload limit")
        raw.extend(chunk)
    return bytes(raw)


async def read_json_body(request: Request, maximum: int = 8000) -> bytes:
    if (
        request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        != "application/json"
    ):
        raise HTTPException(415, "This request must use JSON")
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > maximum:
            raise HTTPException(413, "Workspace request is too large")
        raw.extend(chunk)
    return bytes(raw)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=1, max_length=512, repr=False)


class CreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")


class SelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str = Field(pattern=r"^[a-f0-9]{20}$")


class RecoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Literal["finance_outage", "hostile_note"]
    step: Literal["start", "retry"]


class QuestionRequest(ExecuteRequest):
    question: str = Field(min_length=1, max_length=1000, pattern=r"^[^\x00-\x1f\x7f]+$")


class CaseActionRequest(ExecuteRequest):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    expected_generation: int = Field(ge=0, strict=True)
    status: Literal["checking", "waiting", "resolved"]
    actor: str = Field(min_length=1, max_length=254, pattern=r"^[^\x00-\x1f\x7f]+$")
    note: str = Field(min_length=1, max_length=500, pattern=r"^[^\x00-\x1f\x7f]+$")


class DecisionRequest(ExecuteRequest):
    case_id: str = Field(min_length=1, max_length=40, pattern=r"^[a-z_]+$")
    mode: Literal["text", "visual"] = "text"


class ReplyRequest(ExecuteRequest):
    filename: str = Field(min_length=1, max_length=120, pattern=r"^[^\x00-\x1f\x7f]+$")
    text: str = Field(min_length=12, max_length=8000)


class ApplyReplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    proposal_index: int = Field(ge=0, le=4, strict=True)
    expected_generation: int = Field(ge=0, strict=True)
    status: Literal["checking", "waiting", "resolved"]
    actor: str = Field(min_length=1, max_length=254, pattern=r"^[^\x00-\x1f\x7f]+$")
    note: str = Field(min_length=1, max_length=350, pattern=r"^[^\x00-\x1f\x7f]+$")


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    reviewer: str = Field(min_length=1, max_length=254, pattern=r"^[^\x00-\x1f\x7f]+$")


def _tokens(read=None, execution=None, review=None):
    return {
        "read": read if read is not None else os.getenv("DEMO_API_TOKEN", ""),
        "execute": execution if execution is not None else os.getenv("DEMO_EXECUTION_TOKEN", ""),
        "review": review if review is not None else os.getenv("DEMO_REVIEW_TOKEN", ""),
    }


def _configured(tokens):
    return (
        all(
            isinstance(value, str) and 16 <= len(value) <= 512 and value.strip() == value
            for value in tokens.values()
        )
        and len(set(tokens.values())) == 3
    )


def create_hosted_app(
    workflow: HostedWorkflow,
    *,
    read_token=None,
    execution_token=None,
    review_token=None,
    model_factory=None,
    question_model_factory=None,
    reply_model_factory=None,
    decision_lab=None,
    identity: IapIdentity | None = None,
    password_access: PasswordAccess | None = None,
) -> FastAPI:
    """The optional model factory is trusted test/development configuration, never a request field."""
    if identity is not None and password_access is not None:
        raise ValueError("Choose one workspace authentication mode")
    lab = decision_lab if decision_lab is not None else DecisionLab(workflow)
    tokens = _tokens(read_token, execution_token, review_token)
    app = FastAPI(
        title="Holiday supplier operations", docs_url=None, redoc_url=None, openapi_url=None
    )

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, error):
        if password_access is not None and request.url.path == "/workspace/login":
            return JSONResponse({"detail": "Enter a valid password"}, status_code=422)
        return await request_validation_exception_handler(request, error)

    @app.middleware("http")
    async def private_responses(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if password_access is not None and password_access.origin.startswith("https://"):
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        if "Content-Security-Policy" not in response.headers:
            response.headers["Content-Security-Policy"] = "frame-ancestors 'self'"
        return response

    def authorise(request, authorization, required="read"):
        if password_access is not None:
            finance_request = request.method == "GET" and request.url.path.startswith("/suppliers/")
            if not finance_request or not authorization:
                return password_access.require(request)
        if identity is not None:
            assertion = request.headers.get("x-goog-iap-jwt-assertion")
            if assertion is not None:
                try:
                    principal = identity.verify(assertion)
                except IdentityUnavailable:
                    raise HTTPException(
                        503, "Google identity verification is unavailable"
                    ) from None
                except IdentityDenied:
                    raise HTTPException(
                        403, "Google identity is invalid or not assigned access"
                    ) from None
                if request.method not in {"GET", "HEAD", "OPTIONS"} and (
                    request.headers.get("origin") != str(request.base_url).rstrip("/")
                    or request.headers.get("x-workspace-request") != "1"
                ):
                    raise HTTPException(403, "Workspace request not allowed")
                if required != "read" and principal.role != required:
                    raise HTTPException(403, "Your workspace role cannot perform that action")
                request.state.principal = principal
                return principal.role
            if request.method != "GET" or not request.url.path.startswith("/suppliers/"):
                raise HTTPException(401, "Google workspace sign-in is required")
        if not _configured(tokens):
            raise HTTPException(503, "Separate access passes are not configured")
        parts = authorization.split() if isinstance(authorization, str) else []
        if len(parts) != 2 or parts[0].lower() != "bearer" or len(parts[1]) > 1024:
            raise HTTPException(
                401, "An access pass is required", headers={"WWW-Authenticate": "Bearer"}
            )
        role = next(
            (
                role
                for role, token in tokens.items()
                if hmac.compare_digest(parts[1].encode(), token.encode())
            ),
            None,
        )
        if role is None:
            raise HTTPException(403, "Access denied")
        if (identity is not None or password_access is not None) and role != "read":
            raise HTTPException(403, "Only the finance service credential is allowed here")
        if required != "read" and role != required:
            raise HTTPException(403, "This access pass cannot perform that action")
        return role

    def perform(action):
        try:
            return action()
        except (CaseInputError, ReplyInputError) as error:
            raise HTTPException(422, str(error)) from None
        except ConversationLimit:
            raise HTTPException(
                429,
                "This statement has reached its 20-question limit. Start a new review to continue; earlier answers stay saved.",
            ) from None
        except AllowanceExceeded:
            raise HTTPException(
                429,
                "The workspace daily model allowance is reached. Saved evidence remains available; try again tomorrow.",
            ) from None
        except RequestThrottled:
            raise HTTPException(429, "Wait five seconds between assistant requests") from None
        except SupplierScopeError:
            raise HTTPException(403, "Run outside your supplier scope") from None
        except RunNotFoundError:
            raise HTTPException(404, "Run unavailable") from None
        except (RunBusyError, RunChangedError):
            raise HTTPException(
                409, "Run is busy or has changed. Reload before trying again"
            ) from None
        except CloudStorageError:
            raise HTTPException(
                503, "Saved-run storage is unavailable. Try reading the run again later"
            ) from None
        except ValueError:
            raise HTTPException(
                409, "Run could not be used safely. Check its evidence and reload"
            ) from None
        except Exception:
            raise HTTPException(
                503, "The outcome is unconfirmed. Reload the saved run before retrying"
            ) from None

    def model_ready():
        return model_factory is not None or (
            os.getenv("DEMO_LIVE_APPROVED") == "1"
            and bool(
                os.getenv("GOOGLE_API_KEY")
                or os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "").lower() == "true"
            )
        )

    @app.get("/", response_class=HTMLResponse)
    def workspace():
        from .hosted_workspace import render_hosted_workspace

        html, policy = render_hosted_workspace(
            google_identity=identity is not None, password_mode=password_access is not None
        )
        return HTMLResponse(html, headers={"Content-Security-Policy": policy})

    @app.get("/health")
    def health():
        configured = _configured(tokens)
        return JSONResponse(
            {
                "status": "ok" if configured else "not_configured",
                "data": "synthetic",
                "checks": "process_and_access_configuration_only",
            },
            status_code=200 if configured else 503,
        )

    if password_access is not None:

        @app.post("/workspace/login")
        def login(request: Request, body: LoginRequest):
            password_access.csrf(request)
            token = password_access.login(body.password)
            response = JSONResponse({"available": True})
            response.set_cookie(
                COOKIE,
                token,
                max_age=SESSION_SECONDS,
                secure=True,
                httponly=True,
                samesite="strict",
                path="/",
            )
            return response

        @app.post("/workspace/logout")
        def logout(request: Request):
            password_access.logout(request)
            response = JSONResponse({"signed_out": True})
            response.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="strict")
            return response

        @app.get("/workspace/session")
        def password_session(request: Request):
            password_access.require(request)
            return {
                "available": True,
                "mode": "password",
                "role": "workspace",
                "statement_ref": request.state.statement_ref or perform(workflow.current_reference),
                "expires_in": max(0, int(request.state.session_expires - password_access.clock())),
            }

    if identity is not None:

        @app.get("/workspace/session")
        def workspace_session(request: Request, authorization: str | None = Header(default=None)):
            role = authorise(request, authorization)
            return {
                "available": True,
                "mode": "google",
                "role": {"read": "viewer", "execute": "analyst", "review": "reviewer"}[role],
                "email": request.state.principal.email,
                "statement_ref": perform(workflow.current_reference),
            }

    @app.get("/capabilities")
    def capabilities(request: Request, authorization: str | None = Header(default=None)):
        role = authorise(request, authorization)
        return {
            "supplier_id": workflow.supplier_id,
            "model_mode": "offline_integration" if model_factory is not None else "live",
            "execution_enabled": model_ready(),
            "questions_enabled": question_model_factory is not None or model_ready(),
            "replies_enabled": reply_model_factory is not None or model_ready(),
            "decisions_enabled": getattr(decision_lab, "live_enabled", True)
            if decision_lab is not None
            else bool(os.getenv("OPENROUTER_API_KEY")),
            "permissions": {
                "read": True,
                "create": role in {"execute", "workspace"},
                "execute": role in {"execute", "workspace"},
                "review": role in {"review", "workspace"},
            },
        }

    async def prepared_statement(request, authorization):
        await run_in_threadpool(authorise, request, authorization, "execute")
        raw = await read_statement_body(request)
        try:
            preview = await run_in_threadpool(
                validate_statement, raw, workflow.supplier_id, workflow.data_root / "ledger.csv"
            )
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        return raw, preview

    @app.post("/statements/preview")
    async def preview_statement(request: Request, authorization: str | None = Header(default=None)):
        _, preview = await prepared_statement(request, authorization)
        return {key: value for key, value in preview.items() if key != "result"} | {
            "counts": preview["result"]["counts"],
            "ledger_count": preview["result"]["input_counts"]["ledger"],
            "comparison": "Sample Mallorca finance ledger; not a live company connection",
        }

    @app.post("/statements/import")
    async def import_statement(request: Request, authorization: str | None = Header(default=None)):
        raw, preview = await prepared_statement(request, authorization)
        request_id = request.headers.get("x-request-id", "")
        digest = request.headers.get("x-statement-sha256", "")
        if re.fullmatch(r"[A-Za-z0-9_-]{8,80}", request_id) is None:
            raise HTTPException(422, "A valid import request identifier is required")
        if re.fullmatch(r"[a-f0-9]{64}", digest) is None or not hmac.compare_digest(
            digest, preview["digest"]
        ):
            raise HTTPException(409, "Statement changed; preview the file again before confirming")
        meta = await run_in_threadpool(
            perform, lambda: workflow.import_statement(raw, request_id, digest)
        )
        if password_access is not None:
            await run_in_threadpool(password_access.remember_statement, request, meta["run_id"])
        return meta

    @app.get("/statements/sample")
    def sample_statement(request: Request, authorization: str | None = Header(default=None)):
        authorise(request, authorization)
        raw = perform(
            lambda: (workflow.data_root / "statements" / f"{workflow.supplier_id}.csv").read_bytes()
        )
        return Response(
            raw,
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="mallorca-hotel-statement.csv"'},
        )

    @app.post("/runs/{run_id}/new-review")
    def repeat_statement(
        run_id: RunId,
        request: Request,
        body: ExecuteRequest,
        authorization: str | None = Header(default=None),
    ):
        authorise(request, authorization, "execute")
        meta = perform(lambda: workflow.new_review(run_id, body.request_id))
        if password_access is not None:
            password_access.remember_statement(request, meta["run_id"])
        return meta

    @app.post("/workspace/selection")
    async def select_statement(request: Request, authorization: str | None = Header(default=None)):
        await run_in_threadpool(authorise, request, authorization)
        raw = await read_json_body(request)
        try:
            body = SelectionRequest.model_validate_json(raw)
        except ValueError:
            raise HTTPException(422, "Choose a valid saved statement reference") from None
        await run_in_threadpool(perform, lambda: workflow.read(body.run_id))
        if password_access is not None:
            await run_in_threadpool(password_access.remember_statement, request, body.run_id)
        return {"statement_ref": body.run_id, "persistent": password_access is not None}

    @app.post("/runs/new-review")
    def new_review(
        request: Request, body: ExecuteRequest, authorization: str | None = Header(default=None)
    ):
        authorise(request, authorization, "execute")
        meta = perform(lambda: workflow.create(review_attempt=body.request_id))
        if password_access is not None:
            password_access.remember_statement(request, meta["run_id"])
        return meta

    @app.post("/runs")
    def create(
        request: Request, body: CreateRequest, authorization: str | None = Header(default=None)
    ):
        authorise(request, authorization, "execute")
        return perform(workflow.create)

    @app.get("/runs/{run_id}")
    def read(run_id: RunId, request: Request, authorization: str | None = Header(default=None)):
        authorise(request, authorization)
        data = perform(lambda: workflow.read(run_id))
        return {key: data[key] for key in ("meta", "result", "revision", "execution")} | {
            "draft_text": data["meta"].get("query_text") or render_query(data["result"]),
            "supplier_email": render_supplier_email(data["result"]),
        }

    @app.get("/runs/{run_id}/report", response_class=HTMLResponse)
    def report(run_id: RunId, request: Request, authorization: str | None = Header(default=None)):
        authorise(request, authorization)
        data = perform(lambda: workflow.read(run_id))
        return HTMLResponse(data["report"], headers={"X-Run-Revision": str(data["revision"])})

    @app.get("/runs/{run_id}/evidence", response_class=HTMLResponse)
    def evidence_view(
        run_id: RunId, request: Request, authorization: str | None = Header(default=None)
    ):
        authorise(request, authorization)
        data = perform(lambda: workflow.read(run_id))
        html = perform(lambda: render_report(data["result"], data["meta"]))
        return HTMLResponse(html, headers={"X-Run-Revision": str(data["revision"])})

    @app.post("/runs/{run_id}/execute")
    def execute(
        run_id: RunId,
        request: Request,
        body: ExecuteRequest,
        authorization: str | None = Header(default=None),
    ):
        authorise(request, authorization, "execute")
        if model_factory is None and os.getenv("DEMO_LIVE_APPROVED") != "1":
            raise HTTPException(403, "Live model usage is not enabled by the owner")
        if not model_ready():
            raise HTTPException(503, "Model access is not configured")
        return perform(
            lambda: workflow.execute(
                run_id,
                body.request_id,
                model=model_factory() if model_factory is not None else None,
            )
        )

    @app.post("/workspace/recovery-example")
    async def recovery(request: Request, authorization: str | None = Header(default=None)):
        await run_in_threadpool(authorise, request, authorization)
        raw = await read_json_body(request)
        try:
            body = RecoveryRequest.model_validate_json(raw)
        except ValueError:
            raise HTTPException(422, "Choose an available recovery example") from None
        if body.name == "hostile_note" and body.step != "start":
            raise HTTPException(422, "Choose an available recovery example step")
        return await run_in_threadpool(perform, lambda: recovery_example(body.name, body.step))

    @app.get("/runs/{run_id}/comparison")
    def comparison(
        run_id: RunId,
        request: Request,
        previous: str = Query(pattern=r"^[a-f0-9]{20}$"),
        authorization: str | None = Header(default=None),
    ):
        authorise(request, authorization)
        return perform(lambda: compare_statements(workflow.read(previous), workflow.read(run_id)))

    @app.get("/workspace/comparison-example")
    def sample_comparison(request: Request, authorization: str | None = Header(default=None)):
        authorise(request, authorization)
        return perform(lambda: comparison_example(workflow.data_root, workflow.supplier_id))

    @app.get("/runs/{run_id}/decisions")
    def decision_results(
        run_id: RunId, request: Request, authorization: str | None = Header(default=None)
    ):
        authorise(request, authorization)
        return perform(lambda: lab.read(run_id))

    @app.post("/runs/{run_id}/decisions")
    async def compare_decisions(
        run_id: RunId, request: Request, authorization: str | None = Header(default=None)
    ):
        await run_in_threadpool(authorise, request, authorization, "execute")
        raw = await read_json_body(request, maximum=1500)
        try:
            body = DecisionRequest.model_validate_json(raw)
        except ValueError:
            raise HTTPException(
                422, "Choose a bundled fictional case and a valid comparison reference"
            ) from None
        if decision_lab is None and not os.getenv("OPENROUTER_API_KEY"):
            raise HTTPException(
                503, "Decision providers are not configured; recorded results remain available"
            )
        return await run_in_threadpool(
            perform, lambda: lab.compare(run_id, body.request_id, body.case_id, body.mode)
        )

    @app.get("/runs/{run_id}/replies")
    def replies(run_id: RunId, request: Request, authorization: str | None = Header(default=None)):
        authorise(request, authorization)
        return perform(lambda: workflow.responses.read(run_id))

    @app.post("/runs/{run_id}/replies")
    async def analyse_hotel_reply(
        run_id: RunId, request: Request, authorization: str | None = Header(default=None)
    ):
        await run_in_threadpool(authorise, request, authorization, "execute")
        raw = await read_json_body(request, maximum=40000)
        try:
            body = ReplyRequest.model_validate_json(raw)
        except ValueError:
            raise HTTPException(
                422, "Enter reply text of 12 to 8,000 characters and a short filename"
            ) from None
        if reply_model_factory is None and not model_ready():
            raise HTTPException(503, "Reply model access is not configured")
        return await run_in_threadpool(
            perform,
            lambda: workflow.responses.analyse(
                run_id,
                body.request_id,
                body.text,
                body.filename,
                model=reply_model_factory() if reply_model_factory else None,
            ),
        )

    @app.post("/runs/{run_id}/replies/{reply_id}/apply")
    async def apply_hotel_reply(
        run_id: RunId,
        reply_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        await run_in_threadpool(authorise, request, authorization, "execute")
        raw = await read_json_body(request)
        try:
            body = ApplyReplyRequest.model_validate_json(raw)
        except ValueError:
            raise HTTPException(
                422, "Choose a proposal, enter your name and a note of up to 350 characters"
            ) from None
        return await run_in_threadpool(
            perform, lambda: workflow.responses.apply(run_id, reply_id, **body.model_dump())
        )

    @app.get("/runs/{run_id}/cases")
    def cases(run_id: RunId, request: Request, authorization: str | None = Header(default=None)):
        authorise(request, authorization)
        return perform(lambda: workflow.cases(run_id))

    @app.post("/runs/{run_id}/cases/{evidence_id}/actions")
    async def case_action(
        run_id: RunId,
        evidence_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        await run_in_threadpool(authorise, request, authorization, "execute")
        raw = await read_json_body(request)
        try:
            body = CaseActionRequest.model_validate_json(raw)
        except ValueError:
            raise HTTPException(
                422, "Choose a status, enter your name and a note of up to 500 characters"
            ) from None
        return await run_in_threadpool(
            perform,
            lambda: workflow.case_action(run_id, evidence_id=evidence_id, **body.model_dump()),
        )

    @app.get("/runs/{run_id}/quality")
    def quality(run_id: RunId, request: Request, authorization: str | None = Header(default=None)):
        authorise(request, authorization)
        return perform(lambda: workflow.quality(run_id))

    @app.get("/runs/{run_id}/conversation")
    def conversation(
        run_id: RunId, request: Request, authorization: str | None = Header(default=None)
    ):
        authorise(request, authorization)
        return perform(lambda: workflow.conversation(run_id))

    @app.post("/runs/{run_id}/questions")
    async def ask_question(
        run_id: RunId, request: Request, authorization: str | None = Header(default=None)
    ):
        await run_in_threadpool(authorise, request, authorization, "execute")
        raw = await read_json_body(request)
        try:
            body = QuestionRequest.model_validate_json(bytes(raw))
        except ValueError:
            raise HTTPException(
                422,
                "Use a valid request identifier and a question of 1 to 1,000 printable characters",
            ) from None
        if (
            model_factory is None
            and question_model_factory is None
            and os.getenv("DEMO_LIVE_APPROVED") != "1"
        ):
            raise HTTPException(403, "Live model usage is not enabled by the owner")
        if question_model_factory is None and not model_ready():
            raise HTTPException(503, "Model access is not configured")
        factory = question_model_factory or model_factory
        return await run_in_threadpool(
            perform,
            lambda: workflow.ask(
                run_id,
                body.request_id,
                body.question,
                model=factory() if factory is not None else None,
            ),
        )

    @app.post("/runs/{run_id}/review")
    def review(
        run_id: RunId,
        request: Request,
        body: ReviewRequest,
        authorization: str | None = Header(default=None),
    ):
        authorise(request, authorization, "review")
        reviewer = request.state.principal.email if identity is not None else body.reviewer
        return perform(lambda: workflow.review(run_id, reviewer))

    @app.get("/suppliers/{supplier_id}/payables/{invoice_ref}")
    def payable(
        supplier_id: str,
        invoice_ref: str,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        authorise(request, authorization)
        if supplier_id != workflow.supplier_id:
            raise HTTPException(403, "Supplier outside configured scope")

        def lookup():
            statement = load_statement(
                workflow.data_root / "statements" / f"{workflow.supplier_id}.csv"
            )
            rows = load_ledger(
                workflow.data_root / "ledger.csv", date.fromisoformat(statement["cutoff_date"])
            )
            return [
                row
                for row in rows
                if row["supplier_id"] == supplier_id and row["invoice_ref"] == invoice_ref
            ]

        rows = perform(lookup)
        return {
            "supplier_id": supplier_id,
            "invoice_ref": invoice_ref,
            "source": "simulated_finance_api",
            "payables": rows,
        }

    return app


def create_google_app() -> FastAPI:
    """Construct the service from trusted environment and default Google cloud identity."""
    if not _configured(_tokens()):
        raise ValueError(
            "Configure three distinct access passes before starting the hosted service"
        )
    bucket_name = os.getenv("DEMO_RUN_BUCKET", "")
    if not bucket_name or bucket_name.startswith("gs://") or "/" in bucket_name:
        raise ValueError("Configure the private run bucket name")
    identity = IapIdentity.from_environment()
    encoded = os.getenv("DEMO_PASSWORD_HASH", "")
    origin = os.getenv("DEMO_WORKSPACE_ORIGIN", "")
    password_access = PasswordAccess(None, encoded, origin) if encoded or origin else None
    if identity is not None and password_access is not None:
        raise ValueError("Choose one workspace authentication mode")
    from google.cloud import storage

    try:
        bucket = storage.Client().bucket(bucket_name)
    except Exception:
        raise ValueError("Google cloud identity is unavailable") from None
    workflow = HostedWorkflow(
        CloudRunStore(bucket), os.getenv("DEMO_SUPPLIER_ID", ""), allowance=ModelAllowance(bucket)
    )
    if password_access is not None:
        password_access.bucket = bucket
    return create_hosted_app(workflow, identity=identity, password_access=password_access)
