import asyncio
import json
from pathlib import Path

from test_agent import ScriptedModel

from reconciliation.reply_agent import ReplyTools, analyse_reply
from reconciliation.runs import create_run, load_run

ROOT = Path(__file__).resolve().parents[1]


def saved(tmp_path):
    run = create_run(ROOT / "data/statements/SUP-PMI.csv", ROOT / "data/ledger.csv", tmp_path)
    return load_run(run["run_id"], tmp_path)


def test_reply_analysis_requires_exact_reply_quote_and_renders_server_next_step(tmp_path):
    text = "We issued a credit for INV-0002, please check it against your ledger."
    script = ScriptedModel(
        responses=[
            {"name": "get_reply", "args": {}},
            {"name": "get_invoice", "args": {"invoice_ref": "INV-0002"}},
            {
                "name": "submit_proposal",
                "args": {
                    "answer_json": json.dumps(
                        [{"evidence_id": "R-0002", "quote": text, "next_action": "check_credit"}]
                    )
                },
            },
        ]
    )
    result = asyncio.run(analyse_reply(saved(tmp_path), text, model=script))
    assert result["status"] == "complete"
    assert result["model_calls"] == 3
    proposal = result["proposals"][0]
    assert proposal["invoice_ref"] == "INV-0002"
    assert proposal["suggested_status"] == "checking"
    assert "unverified" in proposal["next_step"]
    assert "does not change balances" in proposal["next_step"]


def test_reply_tools_reject_external_invoice_and_noncontiguous_quote(tmp_path):
    data = saved(tmp_path)["result"]
    tools = ReplyTools(data, "Hotel says INV-0002 was paid in full.")
    tools.get_reply()
    tools.get_invoice("INV-0002")

    class Actions:
        skip_summarization = False

    class Context:
        actions = Actions()

    external = json.dumps(
        [
            {
                "evidence_id": "R-0002",
                "quote": "Hotel says INV-00020 was paid",
                "next_action": "check_payment",
            }
        ]
    )
    assert tools.submit_proposal(external, Context())["accepted"] is False
    unknown = json.dumps(
        [
            {
                "evidence_id": "R-9999",
                "quote": "Hotel says INV-0002 was paid in full.",
                "next_action": "check_payment",
            }
        ]
    )
    assert tools.submit_proposal(unknown, Context())["accepted"] is False

    for suffix in ("-EXTRA", "_EXTRA"):
        boundary = json.dumps(
            [
                {
                    "evidence_id": "R-0002",
                    "quote": f"Hotel says INV-0002{suffix} was paid in full.",
                    "next_action": "check_payment",
                }
            ]
        )
        assert tools.submit_proposal(boundary, Context())["accepted"] is False


def test_reply_tools_reject_matched_and_duplicate_evidence(tmp_path):
    data = saved(tmp_path)["result"]
    tools = ReplyTools(data, "Hotel says INV-0002 was paid in full.")
    tools.get_reply()
    tools.get_invoice("INV-0002")

    class Context:
        class Actions:
            skip_summarization = False

        actions = Actions()

    duplicate = json.dumps(
        [
            {
                "evidence_id": "R-0002",
                "quote": "Hotel says INV-0002 was paid in full.",
                "next_action": "check_payment",
            },
            {
                "evidence_id": "R-0002",
                "quote": "Hotel says INV-0002 was paid in full.",
                "next_action": "check_balance",
            },
        ]
    )
    assert tools.submit_proposal(duplicate, Context())["accepted"] is False

    matched = next(row for row in data["rows"] if row["status"] == "matched")
    matched_tools = ReplyTools(data, f"Hotel says {matched['invoice_ref']} was paid in full.")
    matched_tools.get_reply()
    matched_tools.get_invoice(matched["invoice_ref"])
    proposal = json.dumps(
        [
            {
                "evidence_id": matched["id"],
                "quote": f"Hotel says {matched['invoice_ref']} was paid in full.",
                "next_action": "check_payment",
            }
        ]
    )
    assert matched_tools.submit_proposal(proposal, Context())["accepted"] is False


def test_request_document_is_waiting(tmp_path):
    text = "Please send the receipt for INV-0002 so we can review it."
    script = ScriptedModel(
        responses=[
            {"name": "get_reply", "args": {}},
            {"name": "get_invoice", "args": {"invoice_ref": "INV-0002"}},
            {
                "name": "submit_proposal",
                "args": {
                    "answer_json": json.dumps(
                        [
                            {
                                "evidence_id": "R-0002",
                                "quote": text,
                                "next_action": "request_document",
                            }
                        ]
                    )
                },
            },
        ]
    )
    result = asyncio.run(analyse_reply(saved(tmp_path), text, model=script))
    assert result["status"] == "complete"
    assert result["proposals"][0]["suggested_status"] == "waiting"


def test_reply_call_budget_is_bounded(tmp_path):
    text = "The balance for INV-0002 needs review by your team."
    script = ScriptedModel(responses=[{"name": "get_reply", "args": {}}] * 10)
    result = asyncio.run(analyse_reply(saved(tmp_path), text, model=script))
    assert result["status"] == "call_limit"
    assert result["model_calls"] == 4
    assert script.cursor == 4


def test_provider_failure_and_deadline_never_expose_unchecked_proposals(tmp_path):
    data = saved(tmp_path)
    failure = asyncio.run(
        analyse_reply(
            data, "Hotel says INV-0002 needs checking.", model=ScriptedModel(mode="failure")
        )
    )
    assert failure["status"] == "provider_error" and failure["proposals"] == []
    assert "private-provider" not in repr(failure)
    timed = asyncio.run(
        analyse_reply(
            data,
            "Hotel says INV-0002 needs checking.",
            model=ScriptedModel(mode="timeout"),
            timeout_seconds=0.01,
        )
    )
    assert timed["status"] == "timeout" and timed["proposals"] == []


def test_reply_context_includes_only_exact_matching_differences(tmp_path):
    text = "Please check credit for INV-0002; unrelated INV-00030-EXTRA is not a match."
    tools = ReplyTools(saved(tmp_path)["result"], text)
    context = tools.get_reply()
    assert [row["invoice_ref"] for row in context["matching_differences"]] == ["INV-0002"]
    assert "R-0002" in tools.seen

    class ToolContext:
        class Actions:
            skip_summarization = False

        actions = Actions()

    assert tools.submit_proposal(
        json.dumps([{"evidence_id": "R-0002", "quote": text, "next_action": "check_credit"}]),
        ToolContext(),
    )["accepted"]
