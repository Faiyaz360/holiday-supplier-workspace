import pytest

from reconciliation.showcase import export_showcase


def test_export_contains_only_portable_pages_with_fictional_supplier_evidence(tmp_path):
    output = tmp_path / "site"
    export_showcase(output)
    names = {p.name for p in output.iterdir()}
    assert names == {
        "index.html",
        "mallorca.html",
        "costa-del-sol.html",
        "antalya.html",
        "service-outage.html",
    }
    index = (output / "index.html").read_text(encoding="utf-8")
    for name in names - {"index.html"}:
        assert f'href="{name}"' in index
    mallorca = (output / "mallorca.html").read_text(encoding="utf-8")
    assert "EUR 239.00" in mallorca and "EUR 214.00" in mallorca
    assert 'href="index.html"' in mallorca
    assert "Template draft; model not used" in mallorca
    assert "Simulated service outage; reconciliation preserved" in (
        output / "service-outage.html"
    ).read_text(encoding="utf-8")
    assert "Live run not included" in index


def test_export_refuses_to_overwrite_existing_files(tmp_path):
    existing = tmp_path / "notes.txt"
    existing.write_text("Keep this", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        export_showcase(tmp_path)
    assert existing.read_text() == "Keep this"
    assert not (tmp_path / "index.html").exists()


def test_recorded_run_is_checked_and_export_does_not_modify_original_evidence(tmp_path):
    import hashlib

    from test_runs import make_run

    from reconciliation.runs import load_run, run_folder, save_meta

    meta = make_run(tmp_path)
    source = tmp_path / "outputs"
    saved = load_run(meta["run_id"], source)
    folder = run_folder(meta["run_id"], source)
    with pytest.raises(ValueError, match="completed live"):
        export_showcase(tmp_path / "rejected", folder)
    assert not (tmp_path / "rejected").exists()
    meta["drafting_status"] = "live_model_complete"
    meta["model_calls"] = 4
    meta["events"].extend(
        [
            {"event": "tool_call", "tool": "get_reconciliation"},
            {"event": "tool_call", "tool": "lookup_payable"},
            {"event": "tool_call", "tool": "propose_query"},
            {"event": "drafting_finished", "status": "live_model_complete"},
        ]
    )
    from reconciliation.report import render_query

    meta["query_text"] = render_query(saved["result"])
    save_meta(folder, saved["result"], meta)
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir()}
    export_showcase(tmp_path / "site", folder)
    after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir()}
    assert before == after
    report = (tmp_path / "site/recorded-adk-run.html").read_text(encoding="utf-8")
    assert "Previously recorded live run" in report
    assert "lookup_payable" in report
    assert 'href="recorded-adk-run.html"' in (tmp_path / "site/index.html").read_text(
        encoding="utf-8"
    )


def recorded_example(tmp_path, *, historical=False, private_hotel=False):
    import hashlib
    import json

    from test_runs import make_run

    from reconciliation.report import render_query
    from reconciliation.runs import load_run, run_folder, save_meta

    meta = make_run(tmp_path)
    source = tmp_path / "outputs"
    saved = load_run(meta["run_id"], source)
    if historical:
        next(r for r in saved["result"]["rows"] if r["invoice_ref"] == "INV-UNRECORDED")[
            "overdue"
        ] = False
    if private_hotel:
        saved["result"]["rows"][0]["ledger_lines"][0]["hotel"] = (
            "Private hotel outside the fixtures"
        )
    folder = run_folder(meta["run_id"], source)
    facts = (json.dumps(saved["result"], indent=2, sort_keys=True) + "\n").encode()
    (folder / "result.json").write_bytes(facts)
    meta["result_sha256"] = hashlib.sha256(facts).hexdigest()
    meta["model_calls"] = 3
    meta["drafting_status"] = "live_model_complete"
    meta["query_text"] = render_query(saved["result"])
    meta["events"].extend(
        [
            {"event": "tool_call", "tool": "get_reconciliation"},
            {"event": "tool_call", "tool": "propose_query"},
            {"event": "drafting_finished", "status": "live_model_complete"},
        ]
    )
    save_meta(folder, saved["result"], meta)
    return folder


def test_recorded_fictional_label_cannot_authorise_exporting_different_records(tmp_path):
    folder = recorded_example(tmp_path, private_hotel=True)
    with pytest.raises(ValueError, match="fictional"):
        export_showcase(tmp_path / "site", folder)
    assert not (tmp_path / "site").exists()


def test_historical_due_status_is_explicitly_distinguished_from_current_validation(tmp_path):
    folder = recorded_example(tmp_path, historical=True)
    export_showcase(tmp_path / "site", folder)
    for page in ("index.html", "recorded-adk-run.html"):
        text = (tmp_path / "site" / page).read_text(encoding="utf-8")
        assert "does not validate the current version" in text
