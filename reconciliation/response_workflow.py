"""Source-bound response proposals and explicit human follow-up confirmation."""

import asyncio
import hashlib
import json

from .model_allowance import AllowanceExceeded, RequestThrottled
from .reply_agent import analyse_reply
from .reply_store import ReplyInputError, ReplyStore


def fingerprint(data):
    return hashlib.sha256(
        json.dumps(
            data["result"], sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


def action_id(run_id, reply_id, index):
    return "reply-" + hashlib.sha256(f"{run_id}:{reply_id}:{index}".encode()).hexdigest()[:32]


class HotelResponses:
    def __init__(self, workflow):
        self.workflow = workflow
        self.store = ReplyStore(workflow.store.bucket)

    def read(self, run_id):
        self.workflow.read(run_id)
        value = self.store.read(run_id)
        board = self.workflow.case_store.read(run_id)
        actions = {a["request_id"]: a for a in board["history"]}
        for record in value["replies"]:
            record["applied"] = {
                str(i): actions[action_id(run_id, record["request_id"], i)]
                for i, _ in enumerate((record["outcome"] or {}).get("proposals", []))
                if action_id(run_id, record["request_id"], i) in actions
            }
        return value

    def analyse(self, run_id, request_id, text, filename, *, model=None):
        data = self.workflow.read(run_id)
        claim = self.store.acquire(run_id, request_id, text, filename, fingerprint(data))
        if not claim["claimed"]:
            return claim
        reservation = None
        if self.workflow.allowance is not None:
            try:
                reservation = self.workflow.allowance.reserve(f"reply_{run_id}_{request_id}", 4)
                if not reservation["granted"]:
                    raise ReplyInputError(
                        "This request was already reserved; reload its saved outcome"
                    )
            except (AllowanceExceeded, RequestThrottled) as error:
                outcome = {
                    "status": "rate_limited"
                    if isinstance(error, RequestThrottled)
                    else "allowance_exhausted",
                    "proposals": [],
                    "model_calls": 0,
                    "reported_tokens": None,
                }
                return {"claimed": True, "reply": self.store.finish(run_id, request_id, outcome)}
        outcome = asyncio.run(analyse_reply(data, text, model=model))
        record = self.store.finish(run_id, request_id, outcome)
        confirmation = self.workflow._confirm_usage(reservation, outcome["model_calls"])
        return {"claimed": True, "reply": record, "usage_confirmation": confirmation}

    def apply(self, run_id, reply_id, *, proposal_index, expected_generation, status, actor, note):
        data = self.workflow.read(run_id)
        record = next(
            (r for r in self.store.read(run_id)["replies"] if r["request_id"] == reply_id), None
        )
        if (
            record is None
            or record["status"] != "finished"
            or record["outcome"]["status"] != "complete"
            or record["source_digest"] != fingerprint(data)
        ):
            raise ReplyInputError("Choose a completed proposal for this unchanged statement")
        proposals = record["outcome"]["proposals"]
        if type(proposal_index) is not int or not 0 <= proposal_index < len(proposals):
            raise ReplyInputError("Choose a saved reply proposal")
        proposal = proposals[proposal_index]
        if proposal["quote"] not in record["text"] or not any(
            row["id"] == proposal["evidence_id"]
            and row["invoice_ref"] == proposal["invoice_ref"]
            and row["status"] != "matched"
            for row in data["result"]["rows"]
        ):
            raise ReplyInputError("Reply proposal no longer matches its source evidence")
        if not isinstance(note, str) or not 1 <= len(note.strip()) <= 350:
            raise ReplyInputError("Enter a follow-up note of up to 350 characters")
        return self.workflow.case_action(
            run_id,
            request_id=action_id(run_id, reply_id, proposal_index),
            evidence_id=proposal["evidence_id"],
            expected_generation=expected_generation,
            status=status,
            actor=actor,
            note=f"Hotel reply {reply_id}: {note.strip()}",
        )
