"""Export only fictional HTML pages; never include credentials or runtime files."""

import base64
from copy import deepcopy
from html import escape
from pathlib import Path
from tempfile import TemporaryDirectory

from .acceptance import assess_saved_run
from .fixtures import SUPPLIERS, generate
from .report import render_report
from .runs import create_run, load_run

ROOT = Path(__file__).resolve().parents[1]
PAGES = {"SUP-PMI": "mallorca.html", "SUP-AGP": "costa-del-sol.html", "SUP-AYT": "antalya.html"}


def export_showcase(output: Path, recorded_run: Path | None = None) -> list[str]:
    """Make a separate read-only presentation, without changing any saved evidence."""
    output = Path(output)
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError("Choose a new or empty output directory")
    recorded = None
    if recorded_run is not None:
        recorded_run = Path(recorded_run)
        recorded = load_run(recorded_run.name, recorded_run.parent)
        if recorded["meta"]["drafting_status"] != "live_model_complete":
            raise ValueError("Recorded example must be a completed live run")
        if recorded["result"]["supplier_id"] not in SUPPLIERS or not recorded["result"].get("demo"):
            raise ValueError("Recorded example must use fictional demo supplier data")
    assets = ROOT / "reconciliation/assets"
    css = (assets / "workspace.css").read_text(encoding="utf-8")
    css += (assets / "showcase.css").read_text(encoding="utf-8")
    logo = base64.b64encode((assets / "loveholidays-logo.png").read_bytes()).decode()
    pages = {}
    cards = []
    recorded_note = ""
    with TemporaryDirectory(prefix="holiday-showcase-") as temporary:
        fixtures = Path(temporary)
        generate(fixtures)
        for supplier, (hotel, destination, currency) in SUPPLIERS.items():
            meta = create_run(
                fixtures / f"data/statements/{supplier}.csv",
                fixtures / "data/ledger.csv",
                fixtures / "runs",
            )
            data = load_run(meta["run_id"], fixtures / "runs")
            if recorded and recorded["result"]["supplier_id"] == supplier:
                # The only permitted historical difference is the documented
                # false-to-unknown correction for rows without ledger evidence.
                normalised = deepcopy(recorded["result"])
                for row in normalised["rows"]:
                    if row["overdue"] is False and not row["ledger_lines"]:
                        row["overdue"] = None
                if normalised != data["result"]:
                    raise ValueError("Recorded example differs from the defined fictional records")
                assessment = assess_saved_run(
                    recorded_run.name,
                    recorded_run.parent,
                    fixtures / f"data/statements/{supplier}.csv",
                    fixtures / "data/ledger.csv",
                )
                failures = {key for key, value in assessment["checks"].items() if value is False}
                if failures - {"current_reconciliation_matches"}:
                    raise ValueError("Recorded example fails source or factual-draft checks")
                recorded_note = (
                    "This historical run predates the missing-due-date correction and does not validate the current version."
                    if failures
                    else "Stored facts and the draft pass the current saved-run contract; this is not a fresh live evaluation."
                )
            pages[PAGES[supplier]] = render_report(data["result"], meta | {"showcase_home": True})
            count = sum(v for k, v in data["result"]["counts"].items() if k != "matched")
            cards.append(
                f'<a class="supplier-card" href="{PAGES[supplier]}"><div class="card-top">'
                f'<span class="eyebrow">{escape(currency)} · Hotel supplier</span><span aria-hidden="true">↗</span></div>'
                f"<h2>{escape(destination)}</h2><p>{escape(hotel)}</p>"
                f'<div class="supplier-stats"><span><strong>{data["result"]["counts"].get("matched", 0)}</strong> matched</span>'
                f'<span><strong>{count}</strong> to investigate</span></div><div class="card-bottom">Open statement review <span aria-hidden="true">→</span></div></a>'
            )
            if supplier == "SUP-PMI":
                pages["service-outage.html"] = render_report(
                    data["result"],
                    meta | {"drafting_status": "simulated_provider_outage", "showcase_home": True},
                )
    if recorded:
        pages["recorded-adk-run.html"] = render_report(
            recorded["result"],
            recorded["meta"]
            | {
                "showcase_home": True,
                "recorded_example": True,
                "recorded_validation_note": recorded_note,
            },
        )
        live_link = (
            '<a class="btn primary" href="recorded-adk-run.html">Inspect the recorded ADK run →</a>'
        )
        live_note = (
            "A previously completed Gemini run, with recorded tool calls and token usage. Opening it makes no live requests. "
            + escape(recorded_note)
        )
    else:
        live_link = '<span class="pill">Live run not included</span>'
        live_note = "These examples use factual templates. A verified recorded live run can be included separately."
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"><meta name="color-scheme" content="light"><title>Supplier operations · Holiday statement demo</title><style>{css}</style></head><body>
<a class="skip" href="#main">Skip to supplier examples</a><header class="topbar"><div class="brand"><img src="data:image/png;base64,{logo}" width="210" height="42" alt="loveholidays"><span class="brand-divider" aria-hidden="true"></span><span class="product-name">Supplier operations</span></div><span class="demo-tag">Application demo · fictional data</span></header>
<main class="showcase" id="main"><div class="intro-grid"><section><div class="eyebrow">A holiday supplier operations demo</div><h1>Less chasing.<br>Clearer supplier balances.</h1><p class="intro-copy">Compare a hotel supplier’s statement with open booking records. Find the differences, inspect the evidence and prepare a query for a person to review.</p><div class="intro-tags"><span class="pill">Fictional hotel records</span><span class="pill">Source-linked evidence</span><span class="pill">Human review</span></div></section><aside class="how-card"><div class="eyebrow">Follow one invoice</div><h2>A €25 difference.<br>A clear next step.</h2><div class="balance-demo"><div><span>Supplier statement</span><strong>€239.00</strong></div><div><span>Open ledger</span><strong>€214.00</strong></div></div><p>We show both source records and ask for clarification. A difference does not establish which record is correct.</p><a class="quiet-link" href="mallorca.html#invoices">Inspect invoice INV-0002 →</a></aside></div>
<section class="suppliers" aria-labelledby="supplier-title"><div class="section-title"><div><div class="eyebrow">Choose a destination</div><h2 id="supplier-title">Three suppliers. The same careful checks.</h2></div><span class="pill">Read-only examples</span></div><div class="supplier-grid">{"".join(cards)}</div></section>
<div class="demo-grid"><section class="live-card"><div class="eyebrow">Inside the workflow</div><h2>See the agent’s work.</h2><p>The live workflow uses Google ADK to give Gemini a small set of permitted tools. Code checks the figures; a person reviews the query.</p><p>{live_note}</p>{live_link}</section><section class="recovery-card"><div class="eyebrow">When a service is unavailable</div><h2>The evidence stays useful.</h2><p>A drafting failure should not erase the reconciliation. This explicitly simulated outage shows the preserved records and factual template.</p><a class="btn" href="service-outage.html">Open the recovery example →</a></section></div>
<section class="scope-note"><strong>What you can try</strong><p>Search invoices and bookings, filter findings, expand source evidence, copy a query and export it as a text file. These saved examples do not run a live model, send messages, record new reviews or change balances.</p></section><footer class="footer"><div><strong>Independent application demonstration.</strong> Not an official loveholidays service.<br>All hotels, bookings and invoice records are fictional. No customer data.</div><span>Prepared for a portfolio demonstration</span></footer></main></body></html>"""
    pages["index.html"] = html
    output.mkdir(parents=True, exist_ok=True)
    for name, content in pages.items():
        (output / name).write_text(content, encoding="utf-8")
    return sorted(pages)
