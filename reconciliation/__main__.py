"""Local demo commands. Import the model runtime only for an explicit live run."""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from .fixtures import SUPPLIERS
from .runs import create_run, load_run, review_run, run_folder, save_meta

ROOT = Path(__file__).resolve().parents[1]


def _port(value: str) -> int:
    try:
        port = int(value)
        if 1 <= port <= 65535:
            return port
    except ValueError:
        pass
    raise argparse.ArgumentTypeError("Port must be a number between 1 and 65535")


def main() -> int:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
    parser = argparse.ArgumentParser(description="Fictional holiday supplier reconciliation")
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="Reconcile a synthetic hotel supplier statement")
    demo.add_argument("--supplier", choices=list(SUPPLIERS), default="SUP-PMI")
    demo.add_argument("--output", type=Path, default=ROOT / "outputs")
    modes = demo.add_mutually_exclusive_group()
    modes.add_argument(
        "--live", action="store_true", help="Use Gemini only after approval and credentials"
    )
    modes.add_argument(
        "--simulate-outage",
        action="store_true",
        help="Show an explicitly simulated drafting outage",
    )
    audit = commands.add_parser("audit", help="Check saved facts and workflow without model usage")
    audit.add_argument("run_id")
    audit.add_argument("--output", type=Path, default=ROOT / "outputs")
    audit.add_argument("--supplier", choices=list(SUPPLIERS), default="SUP-PMI")
    review = commands.add_parser("review", help="Record a local human review; sends nothing")
    review.add_argument("run_id")
    review.add_argument("--reviewer", required=True)
    review.add_argument("--output", type=Path, default=ROOT / "outputs")
    server = commands.add_parser("serve", help="Run the authenticated simulated finance API")
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=_port, default=os.getenv("PORT", "8000"))
    hosted = commands.add_parser("serve-hosted", help="Run the Google Cloud supplier workspace")
    hosted.add_argument("--host", default="127.0.0.1")
    hosted.add_argument("--port", type=_port, default=os.getenv("PORT", "8080"))
    showcase = commands.add_parser(
        "showcase", help="Export a portable fictional demo without live requests"
    )
    showcase.add_argument("--output", type=Path, default=ROOT / "outputs/showcase")
    showcase.add_argument(
        "--recorded-run", type=Path, help="Optional verified saved live-run folder"
    )
    commands.add_parser("check-evals", help="Validate ADK evaluation files without model requests")
    args = parser.parse_args()
    try:
        if args.command == "audit":
            from .acceptance import assess_saved_run
            from .source_evidence import resolve_sources

            if load_run(args.run_id, args.output)["result"]["supplier_id"] != args.supplier:
                raise ValueError("The chosen supplier does not match the saved statement")
            statement, ledger = resolve_sources(
                args.run_id,
                args.output,
                ROOT / "data/statements" / f"{args.supplier}.csv",
                ROOT / "data/ledger.csv",
            )
            assessment = assess_saved_run(args.run_id, args.output, statement, ledger)
            print(json.dumps(assessment, indent=2))
            return 0 if assessment["contract_status"] == "passed" else 2
        elif args.command == "showcase":
            from .showcase import export_showcase

            pages = export_showcase(args.output, args.recorded_run)
            print(
                json.dumps(
                    {
                        "path": str(args.output.resolve()),
                        "pages": pages,
                        "live_requests": 0,
                        "published": False,
                    },
                    indent=2,
                )
            )
        elif args.command == "demo":
            meta = create_run(
                ROOT / "data" / "statements" / f"{args.supplier}.csv",
                ROOT / "data" / "ledger.csv",
                args.output,
            )
            if args.live:
                from .agent_runner import execute_run

                meta = asyncio.run(execute_run(meta["run_id"], args.output))
            elif args.simulate_outage:
                data = load_run(meta["run_id"], args.output)
                if meta["review"] is not None or meta["model_calls"]:
                    raise ValueError("Use a separate output folder for outage simulation")
                meta["drafting_status"] = "simulated_provider_outage"
                meta["events"].append(
                    {"event": "simulated_provider_outage", "status": "report_preserved"}
                )
                save_meta(run_folder(meta["run_id"], args.output), data["result"], meta)
            print(
                json.dumps(
                    meta
                    | {
                        "report_path": str((args.output / meta["run_id"] / "report.html").resolve())
                    },
                    indent=2,
                )
            )
            return 0 if not args.live or meta["drafting_status"] == "live_model_complete" else 2
        if args.command == "review":
            print(json.dumps(review_run(args.run_id, args.reviewer, args.output), indent=2))
        elif args.command == "serve":
            import uvicorn

            from .api import create_app

            uvicorn.run(create_app(), host=args.host, port=args.port)
        elif args.command == "serve-hosted":
            import uvicorn

            from .hosted_api import create_google_app

            uvicorn.run(create_google_app(), host=args.host, port=args.port)
        elif args.command == "check-evals":
            from google.adk.evaluation.eval_config import EvalConfig
            from google.adk.evaluation.eval_set import EvalSet

            evaluations = EvalSet.model_validate_json(
                (ROOT / "evals/reconciliation.evalset.json").read_text(encoding="utf-8")
            )
            EvalConfig.model_validate_json(
                (ROOT / "evals/test_config.json").read_text(encoding="utf-8")
            )
            print(
                json.dumps(
                    {
                        "schema_valid": True,
                        "cases": len(evaluations.eval_cases),
                        "live_execution": "not_run",
                    }
                )
            )
        return 0
    except (OSError, ValueError):
        print(
            "The run could not finish. Check inputs, run integrity, supplier scope and the runbook. No messages were sent.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
