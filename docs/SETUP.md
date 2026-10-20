# Run the workspace

Start with the offline workspace. It uses fictional records and makes no provider calls. Model access and cloud hosting are separate, opt-in steps.

## Requirements

- Python 3.13 and uv.
- Node.js 22 or later for browser-state tests and frontend formatting.
- Docker only if you want to check the packaged Linux service.

```sh
uv sync --locked
```

## Interactive offline workspace

```sh
uv run uvicorn decision_preview:create_app --factory --app-dir tests --host 127.0.0.1 --port 8774
```

Open http://localhost:8774/ and use `A fictional password for tests only!`. This is a public fixture password, not a deployed credential. The command binds only to the local machine. Do not deploy this fixture or reuse its password or access passes.

The fixture uses the actual FastAPI application and ADK runner with an in-memory storage stand-in and deterministic models. It disables fresh model comparisons and loads recorded results. Choose **Help → Restart guided tour**. Preview and confirm the sample, open INV-0002's evidence, ask the prepared question, inspect its citation and save a follow-up note.

Restarting the process clears the fixture records. The production storage adapter uses Cloud Storage instead.

## Command-line reports and a static showcase

```sh
uv run python -m reconciliation demo --supplier SUP-PMI
uv run python -m reconciliation demo --supplier SUP-AGP
uv run python -m reconciliation demo --supplier SUP-AYT
uv run python -m reconciliation demo --simulate-outage --output outputs/outage
```

The commands print their report paths. The default uses a factual template with no model request. The fixed fixture cutoff is 18 October 2026. Amount comparisons use outstanding balances, not the original face value of a partly paid invoice.

```sh
uv run python -m reconciliation showcase --output outputs/showcase
uv run python -m http.server 8767 --bind 127.0.0.1 --directory outputs/showcase
```

Open http://127.0.0.1:8767/. Use a new or empty output folder for each export. The showcase is a read-only report presentation, separate from the full workspace.

## CSV input

Use a complete UTF-8 open-item statement, at most 1 MB and 1,000 rows. The interactive workspace is scoped to the configured supplier, initially the fictional Mallorca supplier `SUP-PMI`.

```csv
statement_id,supplier_id,cutoff_date,line_id,invoice_ref,currency,claimed_amount
ST-DEMO-PMI,SUP-PMI,2026-10-18,L-0001,INV-0001,EUR,157.00
```

Dates, currencies, identifiers and decimal amounts must pass validation. Import compares the statement against the sample finance ledger. It does not connect an uploaded file to a real accounting system. Preserve the source currencies; the application performs no foreign-exchange conversion.

## Optional live Gemini investigation

Copy `.env.example` to `.env` and fill the required values privately. Keep live access disabled until you have chosen a provider allowance. The command-line entry point loads `.env`; existing process environment values take precedence.

Set `GOOGLE_API_KEY`, an available `GEMINI_MODEL` and `DEMO_LIVE_APPROVED=1`, then run:

```sh
uv run python -m reconciliation demo --supplier SUP-PMI --live --output outputs/live
```

This command can incur provider charges. A failed or unfinished investigation retains its factual reconciliation and template. Reported usage can be incomplete after provider errors, so it is not an exact bill.

For the ADK development interface:

```sh
uv run adk web --host 127.0.0.1 --port 8001 .
```

Select `reconciler`. Its supplier is fixed at launch by `DEMO_SUPPLIER_ID`. Free-form development responses are model output; the validated report remains the authoritative financial artefact.

## Hosted service configuration

The hosted service uses a private Cloud Storage bucket and application-default Google credentials. Supply secrets at runtime rather than baking them into the image.

| Variable | Purpose |
| --- | --- |
| `DEMO_RUN_BUCKET` | Private Cloud Storage bucket name, without `gs://` |
| `GOOGLE_CLOUD_PROJECT` | Your own Google Cloud project |
| `DEMO_SUPPLIER_ID` | Fixed supplier scope |
| `DEMO_API_TOKEN` | Read/finance pass, at least 16 random characters |
| `DEMO_EXECUTION_TOKEN` | Different pass for investigations |
| `DEMO_REVIEW_TOKEN` | Different pass for human review |
| `DEMO_API_BASE_URL` | Internal loopback finance endpoint, normally `http://127.0.0.1:8080` |
| `DEMO_WORKSPACE_ORIGIN` | Exact HTTPS browser origin for the password workspace |
| `DEMO_PASSWORD_HASH` | scrypt hash of your separately chosen private password |
| `GOOGLE_API_KEY` | Gemini credential |
| `GEMINI_MODEL` | Available Gemini model identifier |
| `DEMO_LIVE_APPROVED` | Explicit opt-in to live model work |
| `OPENROUTER_API_KEY` | Optional specialist model comparisons |

The password service validates the hash format from `reconciliation.password_access`. Configure either password access or the supported verified identity mode, not both. The local fixture is the simplest way to explore the interface without cloud setup.

```sh
docker build --platform linux/amd64 -t holiday-supplier-workspace .
```

The Dockerfile starts `python -m reconciliation serve-hosted --host 0.0.0.0`, listens on `PORT` and runs as an unprivileged user. Cloud Run must provide the runtime configuration, secret references and a service identity with narrowly scoped access to the selected bucket and secrets.

A valid health response confirms process configuration, not end-to-end storage or provider availability. Use separate cloud and model verification before relying on a deployment. Request budgets and billing alerts are not guaranteed provider-wide spending caps.
