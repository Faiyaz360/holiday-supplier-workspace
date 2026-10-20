"""Explicit local paid trial; imports never send requests or load credentials."""

import argparse
import hashlib
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from google.api_core.exceptions import PreconditionFailed  # noqa: E402 - standalone runner adds the app root before local imports.

from reconciliation.decision_evaluation import comparison_summary  # noqa: E402 - standalone runner adds the app root before local imports.
from reconciliation.decision_policy import build_task, case_catalogue, case_labels, rules_decisions  # noqa: E402 - standalone runner adds the app root before local imports.
from reconciliation.decision_provider import MODELS, run_decision, run_gemini_baseline  # noqa: E402 - standalone runner adds the app root before local imports.
from reconciliation.decision_store import DecisionBudget, DecisionStore  # noqa: E402 - standalone runner adds the app root before local imports.

RUN = "d" * 20


class FileBucket:
    """Local trial only; runner holds an exclusive lock for the entire invocation."""

    def __init__(self, root):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def blob(self, name):
        return FileBlob(self.root, name)

    def get_blob(self, name, **kwargs):
        b = self.blob(name)
        if not b.path.exists():
            return None
        d = json.loads(b.path.read_text(encoding="utf-8"))
        b.generation = d["generation"]
        b.size = len(d["payload"].encode())
        return b


class FileBlob:
    def __init__(self, root, name):
        self.path = root / (hashlib.sha256(name.encode()).hexdigest() + ".json")
        self.generation = None
        self.size = None

    def download_as_bytes(self, *, if_generation_match, **kwargs):
        d = json.loads(self.path.read_text(encoding="utf-8"))
        if d["generation"] != if_generation_match:
            raise PreconditionFailed("Local trial changed")
        return d["payload"].encode("utf-8")

    def upload_from_string(self, data, *, if_generation_match, **kwargs):
        old = (
            json.loads(self.path.read_text(encoding="utf-8"))
            if self.path.exists()
            else {"generation": 0}
        )
        if old["generation"] != if_generation_match:
            raise PreconditionFailed("Local trial changed")
        value = json.dumps(
            {"generation": old["generation"] + 1, "payload": data}, ensure_ascii=False
        )
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(value, encoding="utf-8")
        os.replace(temporary, self.path)


def check_prices():
    with urllib.request.urlopen(
        "https://openrouter.ai/api/v1/models?output_modalities=decisions", timeout=20
    ) as response:
        rows = json.load(response)["data"]
    info = {r["id"]: r for r in rows if r["id"] in MODELS}
    if set(info) != set(MODELS):
        raise ValueError("Shortlisted model missing from current catalogue")
    for row in info.values():
        rate = float(row["pricing"]["prompt"])
        if not 0 <= rate <= 0.042 / 1_000_000:
            raise ValueError("Posted model rate changed; pause for review")
    return {
        model: {
            "prompt_usd_per_token": r["pricing"]["prompt"],
            "input_modalities": r.get("architecture", {}).get("input_modalities", []),
        }
        for model, r in info.items()
    }


def main():
    parser = argparse.ArgumentParser(
        description="Compare fictional hotel decisions within the approved initial allowance"
    )
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--cases", nargs="+", default=["credit"])
    parser.add_argument("--include-gemini", action="store_true")
    parser.add_argument("--attempt", default="initial")
    args = parser.parse_args()
    folder = ROOT / "outputs/decision-trial"
    folder.mkdir(parents=True, exist_ok=True)
    if not args.live:
        print(
            "Offline mode: no credentials loaded and no network calls. Add --live for an approved trial."
        )
        return 0
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
    if args.include_gemini:
        os.environ["DEMO_LIVE_APPROVED"] = "1"
    lock = folder / "trial.lock"
    try:
        handle = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        print("Trial lock exists; inspect saved outcomes before starting another run.")
        return 2
    try:
        metadata = check_prices()
        bucket = FileBucket(folder / "records")
        store = DecisionStore(bucket)
        budget = DecisionBudget(bucket)
        for old in store.read(RUN)["comparisons"]:
            if old["status"] in {"pending", "uncertain"} and any(
                r.get("status") in {"invalid_answer", "provider_error", "timeout"}
                for r in old["results"].values()
            ):
                store.finish(RUN, old["request_id"])
        (folder / "catalogue.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        for case_id in args.cases:
            task = build_task(case_id)
            if task["mode"] == "visual" and any(
                "image" not in metadata[m]["input_modalities"] for m in MODELS
            ):
                print("Visual input not advertised by both models; no request sent.")
                return 2
            digest = hashlib.sha256(
                json.dumps(task, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()
            request_id = (
                "trial-" + hashlib.sha256((digest + args.attempt).encode()).hexdigest()[:32]
            )
            claim = store.acquire(RUN, request_id, digest, case_id)
            if not claim["claimed"]:
                print(case_id + ": recorded result reused; no paid rerun")
                continue
            store.save_result(
                RUN,
                request_id,
                "rules",
                rules_decisions(task)
                if task["mode"] == "text"
                else {
                    "status": "not_supported",
                    "answers": {},
                    "elapsed_ms": 0,
                    "cost": {"usd": 0, "kind": "not_called"},
                },
            )
            order = list(MODELS)
            if len(store.read(RUN)["comparisons"]) % 2 == 0:
                order.reverse()
            for model in order:
                name = "jev" if model == MODELS[0] else "d1_ultra"
                token = hashlib.sha256(f"{request_id}:{name}".encode()).hexdigest()
                budget.reserve(token)
                result = run_decision(task, model)
                store.save_result(
                    RUN,
                    request_id,
                    name,
                    result,
                    finish=model == order[-1]
                    and not (args.include_gemini and task["mode"] == "text"),
                )
                budget.settle(token, result)
                print(
                    json.dumps(
                        {
                            "case": case_id,
                            "model": model,
                            "status": result["status"],
                            "cost": result["cost"],
                            "elapsed_ms": result["elapsed_ms"],
                        }
                    )
                )
                save_report(store, budget, folder)
                if budget.read()["stopped"] or result["status"] not in {
                    "complete",
                    "not_supported",
                }:
                    store.finish(RUN, request_id)
                    save_report(store, budget, folder)
                    print("Paused after provider failure or uncertain usage. No automatic retry.")
                    return 2
            if args.include_gemini and task["mode"] == "text":
                result = run_gemini_baseline(task)
                store.save_result(RUN, request_id, "gemini", result, finish=True)
                print(
                    json.dumps(
                        {
                            "case": case_id,
                            "model": "Gemini",
                            "status": result["status"],
                            "cost": result["cost"],
                            "elapsed_ms": result["elapsed_ms"],
                        }
                    )
                )
                save_report(store, budget, folder)
                if result["status"] not in {"complete", "not_supported"}:
                    return 2
        save_report(store, budget, folder)
        print(json.dumps({"budget": budget.read(), "report": "outputs/decision-trial/report.json"}))
        return 0
    finally:
        os.close(handle)
        lock.unlink(missing_ok=True)


def save_report(store, budget, folder):
    saved = store.read(RUN)
    labels = {
        c["id"]: case_labels(c["id"])["expected"] for c in case_catalogue() if c["mode"] == "text"
    }
    value = saved | {
        "budget": budget.read(),
        "label_status": "author_draft",
        "labels": labels,
        "summary": comparison_summary(saved["comparisons"], labels),
    }
    (folder / "report.json").write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, KeyError, TimeoutError) as error:
        print(
            "Trial stopped ("
            + type(error).__name__
            + "): configuration, response or allowance needs review. No automatic retry."
        )
        raise SystemExit(2) from None
