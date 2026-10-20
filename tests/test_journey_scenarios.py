import re
from pathlib import Path

from test_hosted_api import EXECUTE, auth, client


def test_all_scenario_claims_match_the_real_sample_and_preserve_every_row():
    http, _ = client()
    raw = http.get("/statements/sample", headers=auth(EXECUTE)).content
    preview = http.post(
        "/statements/preview", content=raw, headers=auth(EXECUTE) | {"Content-Type": "text/csv"}
    ).json()
    run = http.post("/runs", json={}, headers=auth(EXECUTE)).json()
    data = http.get("/runs/" + run["run_id"], headers=auth(EXECUTE)).json()
    source = (Path(__file__).parents[1] / "reconciliation/assets/experience.js").read_text(
        encoding="utf-8"
    )
    definitions = re.findall(
        r'invoice:\s*["\']([^"\']+)["\'],\s*finding:\s*["\']([^"\']+)["\']', source
    )
    assert len(definitions) == 3
    rows = {row["invoice_ref"]: row for row in data["result"]["rows"]}
    for invoice, finding in definitions:
        assert rows[invoice]["status"] == finding
    credit = rows["INV-0002"]
    assert credit["statement_lines"][0]["claimed_amount"] == "239.00"
    assert credit["ledger_lines"][0]["outstanding_amount"] == "214.00"
    currency = rows["INV-0003"]
    assert currency["statement_lines"][0]["currency"] == "GBP"
    assert currency["ledger_lines"][0]["currency"] == "EUR"
    assert len(rows["INV-0004"]["statement_lines"]) == 2
    assert preview["counts"] == data["result"]["counts"]
