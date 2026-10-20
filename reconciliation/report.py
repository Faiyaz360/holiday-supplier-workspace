"""Render factual reports and supplier queries without model-generated money."""

import base64
import hashlib
from datetime import date
from html import escape
from pathlib import Path
from string import Template

INTRODUCTIONS = (
    "Please help us resolve the following differences in the attached statement.",
    "We have compared your statement with our open supplier ledger and need clarification.",
    "Please review the invoice queries below and confirm the correct outstanding balances.",
)


def evidence_lines(row: dict) -> list[str]:
    lines = []
    for side, records, amount in [
        ("Statement", row["statement_lines"], "claimed_amount"),
        ("Ledger", row["ledger_lines"], "outstanding_amount"),
    ]:
        for record in records:
            context = (
                f"; {record['hotel']}, {record['destination']}; stay {record['stay_date']}; booking {record['booking_ref']}"
                if side == "Ledger"
                else ""
            )
            original = (
                f"; Original invoice: {record['currency']} {record['original_amount']}"
                if side == "Ledger" and record["original_amount"] != record["outstanding_amount"]
                else ""
            )
            lines.append(
                f"{side} CSV row {record['source_row']}: {record['currency']} {record[amount]}{original}{context}"
            )
    return lines


def render_query(
    result: dict, exception_ids: list[str] | None = None, introduction: str = INTRODUCTIONS[0]
) -> str:
    if introduction not in INTRODUCTIONS:
        raise ValueError("Unsupported introduction; use an approved factual opening")
    exceptions = {row["id"]: row for row in result["rows"] if row["status"] != "matched"}
    selected = list(exceptions) if exception_ids is None else exception_ids
    if len(set(selected)) != len(selected) or any(key not in exceptions for key in selected):
        raise ValueError("Unknown, matched or duplicate exception ID")
    parts = [
        f"DRAFT FOR HUMAN REVIEW | {result['supplier_id']} | {result['statement_id']}",
        "Fictional travel records. No message has been sent."
        if result.get("demo", True)
        else "Uploaded statement compared with a sample finance ledger. No message has been sent.",
        introduction,
    ]
    for key in selected:
        row = exceptions[key]
        parts.append(f"\n{key} | {row['invoice_ref']} | {row['status'].replace('_', ' ')}")
        parts.extend(evidence_lines(row))
    if not selected:
        parts.append("No discrepancies found within this statement's documented scope.")
    parts.append(
        "\nPlease confirm these items before any account adjustment. This draft authorises no payment."
    )
    return "\n".join(parts)


STATUS_LABELS = {
    "matched": "Matched",
    "amount_mismatch": "Amount difference",
    "currency_mismatch": "Currency difference",
    "ambiguous": "Duplicate / ambiguous",
    "statement_only": "Statement only",
    "ledger_only": "Ledger only",
}

NEXT_STEPS = {
    "matched": "The currency and outstanding amount agree within this statement's scope.",
    "amount_mismatch": "Check the agreed balance and any credits or partial payments with the supplier.",
    "currency_mismatch": "Confirm the invoice currency before comparing balances. No exchange rate has been applied.",
    "ambiguous": "Check every source line. Repeated references cannot safely be treated as a single confirmed invoice.",
    "statement_only": "Ask for the invoice and booking details. No matching open ledger record or due date was found.",
    "ledger_only": "Confirm why this open ledger invoice is missing from the supplier statement.",
}


def render_supplier_email(result: dict) -> str:
    """Present the same saved exceptions as a readable email; never recompute money."""
    parts = [
        f"Subject: Invoice queries - {result['statement_id']}",
        "Fictional travel records - for review only. No message has been sent."
        if result.get("demo", True)
        else "Uploaded statement checked against a sample ledger - for review only. No message has been sent.",
        "\nHello,",
        f"Please help us confirm the following items on statement {result['statement_id']}.",
    ]
    requests = {
        "amount_mismatch": "Please confirm the outstanding balance and provide any credit notes or payment details that explain the difference.",
        "currency_mismatch": "Please confirm the correct invoice currency. We have not converted either amount.",
        "ambiguous": "Please confirm whether these repeated entries refer to one invoice or separate invoices, and send the supporting documents.",
        "statement_only": "Please send a copy of this invoice and its booking details so we can identify the corresponding finance record.",
        "ledger_only": "Please confirm why this open invoice is missing from your statement and whether you expect it to remain outstanding.",
    }
    exceptions = [row for row in result["rows"] if row["status"] != "matched"]
    for number, row in enumerate(exceptions, 1):
        parts.append(f"\n{number}. {row['invoice_ref']} - {STATUS_LABELS[row['status']]}")
        for label, records, field in [
            ("Statement", row["statement_lines"], "claimed_amount"),
            ("Our ledger", row["ledger_lines"], "outstanding_amount"),
        ]:
            amounts = "; ".join(f"{record['currency']} {record[field]}" for record in records)
            parts.append(f"{label}: {amounts or 'Not recorded'}")
        parts.append(requests[row["status"]])
    if not exceptions:
        parts.append(
            "\nNo invoice differences were found within this statement's documented scope."
        )
    parts.extend(
        [
            "\nPlease confirm these items before any account adjustment.",
            "This draft does not authorise payment.",
        ]
    )
    return "\n".join(parts)


DRAFTING_LABELS = {
    "offline_template": "Template draft; model not used",
    "simulated_provider_outage": "Simulated service outage; reconciliation preserved",
    "live_model_complete": "Model-assisted draft ready for review",
    "offline_model_verified": "Offline model test; no live provider used",
    "provider_error": "Drafting service failed; factual template available",
    "timeout": "Drafting timed out; factual template available",
    "call_limit": "Request limit reached; factual template available",
    "live_not_enabled": "Live model use is not enabled",
    "missing_credentials": "Model access is not configured",
    "incomplete_draft": "Draft incomplete; full factual template shown",
}


def render_report(result: dict, run: dict) -> str:
    """Render a portable workspace; only its hash-approved local script may execute."""

    def e(value):
        return escape(str(value), quote=True)

    assets = Path(__file__).with_name("assets")
    css = (assets / "workspace.css").read_text(encoding="utf-8")
    css += (assets / "showcase.css").read_text(encoding="utf-8")
    script = (assets / "workspace.js").read_text(encoding="utf-8")
    script_hash = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    csp = (
        "default-src 'none'; img-src data:; style-src 'unsafe-inline'; "
        f"script-src 'sha256-{script_hash}'; base-uri 'none'; form-action 'none'"
    )
    exceptions = [row for row in result["rows"] if row["status"] != "matched"]
    ledger = [line for row in result["rows"] for line in row["ledger_lines"]]
    destinations = sorted({line["destination"] for line in ledger})
    hotels = sorted({line["hotel"] for line in ledger})
    destination = ", ".join(destinations) or "Destination not recorded"
    supplier_name = ", ".join(hotels) or result["supplier_id"]

    def amounts(records, field):
        if not records:
            return '<span class="missing">Not recorded</span>'
        return "<br>".join(
            f'<span class="money">{e(line["currency"])} {e(line[field])}</span>' for line in records
        )

    invoice_rows = []
    for row in result["rows"]:
        payables = row["ledger_lines"]
        booking = ", ".join(dict.fromkeys(line["booking_ref"] for line in payables))
        due_dates = sorted({line["due_date"] for line in payables})
        due = (
            "Due date unknown"
            if not due_dates
            else f"Due {', '.join(due_dates)}"
            + (" · Overdue at cutoff" if row["overdue"] else " · Not past due at cutoff")
        )
        evidence = "".join(f"<li>{e(line)}</li>" for line in evidence_lines(row))
        # Matched rows are available through the filter. Without scripting, show all.
        hidden = " hidden" if row["status"] == "matched" else ""
        invoice_rows.append(
            f'<tr data-invoice-ref="{e(row["invoice_ref"])}" data-status="{e(row["status"])}"{hidden}>'
            f'<td><div class="invoice-ref">{e(row["invoice_ref"])}</div>'
            f'<div class="meta">{e(booking or "No booking in ledger")}</div>'
            f'<div class="meta">{e(row["id"])}</div></td>'
            f'<td><span class="badge {e(row["status"])}">{e(STATUS_LABELS[row["status"]])}</span>'
            f'<details class="evidence"><summary>View evidence <span class="visually-hidden">for {e(row["invoice_ref"])}</span></summary>'
            f'<div class="evidence-body"><p><strong>{e(due)}</strong></p>'
            f"<p>{e(NEXT_STEPS[row['status']])}</p><ul>{evidence}</ul></div></details></td>"
            f"<td>{amounts(row['statement_lines'], 'claimed_amount')}</td>"
            f"<td>{amounts(payables, 'outstanding_amount')}</td></tr>"
        )
    totals_rows = "".join(
        f"<tr><td>{e(STATUS_LABELS[status])}</td><td>{e(currency)}</td>"
        f"<td>{e(values['statement'])}</td><td>{e(values['ledger'])}</td></tr>"
        for status, currencies in result["totals"].items()
        for currency, values in currencies.items()
    )
    status_options = "".join(
        f'<option value="{e(status)}">{e(label)} ({result["counts"].get(status, 0)})</option>'
        for status, label in STATUS_LABELS.items()
        if status != "matched" and result["counts"].get(status, 0)
    )
    review = run.get("review")
    drafting_status = run.get("drafting_status", "offline_template")
    matched = result["counts"].get("matched", 0)
    values = {
        "data_label": "Application demo · fictional data"
        if result.get("demo", True)
        else "Uploaded statement · sample finance ledger",
        "data_note": "Fictional travel records.<br>No customer information."
        if result.get("demo", True)
        else "User-provided statement.<br>Compared with a sample finance ledger.",
        "data_footer": "Made-up hotel, booking and invoice records. No emails sent. No accounts changed."
        if result.get("demo", True)
        else "User-provided statement and sample finance records. No emails sent. No accounts changed.",
        "home_link": '<p class="home-link"><a class="quiet-link" href="index.html">← All supplier examples</a></p>'
        if run.get("showcase_home")
        else "",
        "recorded_notice": '<div class="recorded-notice"><strong>Previously recorded live run.</strong> This is a read-only view of a completed ADK and Gemini workflow. Opening this page makes no model requests. Original saved evidence is unchanged. '
        + e(run.get("recorded_validation_note", ""))
        + "</div>"
        if run.get("recorded_example")
        else "",
        "tool_history": '<details class="tool-history"><summary>Inspect recorded tool calls</summary><ol>'
        + "".join(
            f"<li><code>{e(event['tool'])}</code></li>"
            for event in run.get("events", [])
            if isinstance(event, dict)
            and event.get("event") == "tool_call"
            and isinstance(event.get("tool"), str)
            and event.get("tool")
            in {"get_reconciliation", "get_exception", "lookup_payable", "propose_query"}
        )
        + "</ol><p>Only permitted tool names are shown. Raw model text, credentials and service responses are excluded. No recorded calls means this run did not record a tool request.</p></details>",
        "csp": csp,
        "css": css,
        "script": script,
        "logo": base64.b64encode((assets / "loveholidays-logo.png").read_bytes()).decode(),
        "destination": e(destination),
        "supplier_name": e(supplier_name),
        "supplier_id": e(result["supplier_id"]),
        "statement_id": e(result["statement_id"]),
        "cutoff": e(date.fromisoformat(result["cutoff_date"]).strftime("%d %b %Y")),
        "matched": matched,
        "exceptions": len(exceptions),
        "group_count": len(result["rows"]),
        "matched_rate": f"{100 * matched / len(result['rows']):.1f}" if result["rows"] else "0.0",
        "ledger_count": result["input_counts"]["ledger"],
        "statement_count": result["input_counts"]["statement"],
        "review": "Reviewed locally" if review else "Awaiting human review",
        "status_label": e(DRAFTING_LABELS.get(drafting_status, "Check run status")),
        "warning": "warning"
        if drafting_status
        not in {"offline_template", "live_model_complete", "offline_model_verified"}
        else "",
        "review_step_class": "" if review else "pending",
        "review_step_mark": "✓" if review else "3",
        "review_step_title": "Reviewed locally" if review else "Your review is next",
        "review_step_text": e(
            f"Recorded by {review['reviewer']}. Nothing has been sent."
            if review
            else "Inspect the evidence and draft. Review is recorded separately by an authorised person."
        ),
        "draft_scope": "Read the complete draft below to check which invoice groups are included.",
        "draft": e(run.get("query_text") or render_query(result)),
        "invoice_rows": "".join(invoice_rows),
        "totals_rows": totals_rows,
        "status_options": status_options,
        "run_id": e(run["run_id"]),
        "drafting_mode": e(DRAFTING_LABELS.get(drafting_status, "Check run status")),
        "model_calls": e(run.get("model_calls", 0)),
        "duration": e(run.get("duration_seconds", "unavailable")),
        "model": e(run.get("model", "Not used")),
        "tokens": e(
            run.get("total_tokens") if run.get("total_tokens") is not None else "Unavailable"
        ),
        "assumption": e(result["assumption"]),
    }
    return Template((assets / "workspace.html").read_text(encoding="utf-8")).substitute(values)
