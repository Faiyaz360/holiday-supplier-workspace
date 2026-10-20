"""Run the real ADK event loop; preserve reconciled facts on model failure."""

import asyncio
import os
import time
from importlib.metadata import version
from pathlib import Path

from google.adk.agents.invocation_context import LlmCallsLimitExceededError
from google.adk.agents.run_config import RunConfig
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.workflow import NodeTimeoutError
from google.genai import types

from .adk_agent import CallLimitError, LiveNotEnabledError, Meter, build_agent
from .runs import load_run, run_folder, save_meta
from .tools import FinanceClient, ReconciliationTools


async def execute_run(run_id: str, output: Path, model=None, timeout_seconds: float = 120) -> dict:
    folder = run_folder(run_id, output)
    lock = folder / ".run.lock"
    try:
        lock_handle = lock.open("x", encoding="utf-8")
    except FileExistsError:
        raise ValueError("Run is busy; retry later") from None
    try:
        data = load_run(run_id, output)
        meta = data["meta"]
        if meta["review"] is not None:
            raise ValueError("Reviewed reports are frozen; use new inputs for a new report")
        meter = Meter(
            calls=meta.get("model_calls", 0),
            tokens=meta.get("total_tokens"),
            timeout=min(timeout_seconds, 120),
        )
        meta.pop("query_text", None)
        finance = None
        if os.getenv("DEMO_API_BASE_URL"):
            finance = FinanceClient(
                os.environ["DEMO_API_BASE_URL"],
                os.getenv("DEMO_API_TOKEN", ""),
                data["result"]["supplier_id"],
            )
        tools = ReconciliationTools(data, finance)
        agent = build_agent(tools, model=model, meter=meter)
        meta["model"] = agent.model.model
        meta["adk_version"] = version("google-adk")
        meta["evidence_source"] = "simulated_finance_api" if finance else "local_ledger_snapshot"
        status = "provider_error"

        async def invoke():
            session_service = InMemorySessionService()
            session = await session_service.create_session(
                app_name="reconciler", user_id="local_demo"
            )
            async with Runner(
                app_name="reconciler", agent=agent, session_service=session_service
            ) as runner:
                async for _ in runner.run_async(
                    user_id="local_demo",
                    session_id=session.id,
                    new_message=types.Content(
                        role="user",
                        parts=[
                            types.Part(
                                text="Reconcile this supplier statement, investigate the exceptions and propose a query covering every exception for human review."
                            )
                        ],
                    ),
                    run_config=RunConfig(max_llm_calls=10),
                ):
                    pass

        try:
            if model is None and os.getenv("DEMO_LIVE_APPROVED") != "1":
                status = "live_not_enabled"
            elif model is None and not (
                os.getenv("GOOGLE_API_KEY")
                or os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "").lower() == "true"
            ):
                status = "missing_credentials"
            else:
                await asyncio.wait_for(invoke(), timeout=meter.timeout)
                expected = {
                    row["id"] for row in data["result"]["rows"] if row["status"] != "matched"
                }
                if tools.proposal is not None and set(tools.proposal["exception_ids"]) == expected:
                    meta["query_text"] = tools.proposal["text"]
                    status = (
                        "offline_model_verified" if model is not None else "live_model_complete"
                    )
                else:
                    status = "incomplete_draft"
        except (CallLimitError, LlmCallsLimitExceededError):
            status = "call_limit"
        except LiveNotEnabledError:
            status = "live_not_enabled"
        except (TimeoutError, NodeTimeoutError):
            status = "timeout"
        except Exception:
            # Provider and tool exception text may contain credentials; retain only a category.
            status = "provider_error"
        meta["drafting_status"] = status
        meta["model_calls"] = meter.calls
        meta["total_tokens"] = meter.tokens
        meta["served_model_version"] = meter.model_version
        meta["duration_seconds"] = round(time.monotonic() - meter.started, 3)
        meta["events"].extend(meter.events + [{"event": "drafting_finished", "status": status}])
        save_meta(folder, data["result"], meta)
        return meta
    finally:
        lock_handle.close()
        lock.unlink(missing_ok=True)
