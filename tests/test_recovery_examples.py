from test_hosted_api import READ, auth, client


def example(name, step):
    from reconciliation.recovery_examples import recovery_example

    return recovery_example(name, step)


def test_finance_outage_and_retry_use_real_client_boundary_and_preserve_evidence():
    failed = example("finance_outage", "start")
    assert failed["state"] == "provider_error"
    assert failed["evidence_preserved"] is True
    assert failed["model_calls"] == 0 and failed["isolated_example"] is True
    recovered = example("finance_outage", "retry")
    assert recovered["state"] == "recovered"
    assert recovered["evidence_preserved"] is True
    assert recovered["invoice_ref"] == "INV-0002"
    assert recovered["outstanding_amount"] == "214.00"


def test_hostile_cell_cannot_switch_scope_or_add_tools():
    result = example("hostile_note", "start")
    assert result["state"] == "scope_preserved"
    assert result["supplier_id"] == "SUP-PMI"
    assert result["cross_supplier_read_blocked"] is True
    assert result["model_calls"] == 0
    assert "not a live model test" in result["limits"]


def test_example_endpoint_cannot_change_normal_workspace():
    http, workflow = client()
    run = workflow.create()["run_id"]
    before = dict(workflow.store.bucket.objects)
    assert (
        http.post(
            "/workspace/recovery-example", json={"name": "finance_outage", "step": "start"}
        ).status_code
        == 401
    )
    response = http.post(
        "/workspace/recovery-example",
        json={"name": "finance_outage", "step": "retry"},
        headers=auth(READ),
    )
    assert response.status_code == 200 and response.json()["state"] == "recovered"
    assert workflow.store.bucket.objects == before
    bad = http.post(
        "/workspace/recovery-example",
        json={"name": "disable_real_service", "step": "start"},
        headers=auth(READ),
    )
    assert bad.status_code == 422
    assert workflow.read(run)["meta"]["model_calls"] == 0
