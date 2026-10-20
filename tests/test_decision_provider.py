import copy
import json

import httpx
import pytest

from reconciliation.decision_policy import build_task
from reconciliation.decision_provider import run_decision, validate_answers


def answer_for(task):
    result = {}
    for key, q in task["questions"].items():
        if q["type"] == "noul":
            result[key] = {"type": "noul", "noul": 0.9}
        elif q["type"] == "choice":
            result[key] = {
                "type": "choice",
                "choice": "investigate_evidence",
                "probabilities": {k: float(k == "investigate_evidence") for k in q["criteria"]},
                "confidence": 1.0,
            }
        else:
            result[key] = {
                "type": "score",
                "score": 1.0,
                "probabilities": {str(i): float(i == 1) for i in range(4)},
                "legend": {str(i): v for i, v in enumerate(q["criteria"])},
                "confidence": 1.0,
            }
    return result


def test_identical_payloads_validated_usage_and_no_secret_leak(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "private-test-key")
    payloads = []

    def handle(req):
        data = json.loads(req.content)
        payloads.append(data)
        return httpx.Response(
            200,
            json={
                "model": data["model"],
                "provider": "Test",
                "answers": answer_for(build_task("credit")),
                "usage": {"input_tokens": 100, "output_tokens": 0, "cost": 0.0000042},
            },
        )

    for model in ["typesafe/jev-1.14", "liquid/d1-ultra"]:
        r = run_decision(build_task("credit"), model, transport=httpx.MockTransport(handle))
        assert r["status"] == "complete"
        assert r["cost"]["kind"] == "reported" and r["cost"]["usd"] == 0.0000042
        assert "private-test-key" not in json.dumps(r)
    assert {k: v for k, v in payloads[0].items() if k != "model"} == {
        k: v for k, v in payloads[1].items() if k != "model"
    }


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), True, -0.01, 1.01])
def test_invalid_probability_rejected(bad):
    t = build_task("credit")
    a = answer_for(t)
    a["credit"]["noul"] = bad
    with pytest.raises(ValueError):
        validate_answers(t["questions"], a)


@pytest.mark.parametrize("change", ["missing", "extra", "wrong_sum", "wrong_choice", "wrong_score"])
def test_invalid_shape_or_inconsistent_answer_rejected(change):
    t = build_task("credit")
    a = copy.deepcopy(answer_for(t))
    if change == "missing":
        del a["payment"]
    if change == "extra":
        a["other"] = {"noul": 0.8}
    if change == "wrong_sum":
        a["priority"]["probabilities"]["0"] = 0.5
    if change == "wrong_choice":
        a["next_step"]["choice"] = "send_payment"
    if change == "wrong_score":
        a["priority"]["score"] = 3
    with pytest.raises(ValueError):
        validate_answers(t["questions"], a)


@pytest.mark.parametrize("status", [301, 401, 402, 429, 500])
def test_provider_failures_never_retry_or_echo_error(monkeypatch, status):
    monkeypatch.setenv("OPENROUTER_API_KEY", "private-test-key")
    calls = []

    def handle(req):
        calls.append(1)
        return httpx.Response(
            status,
            text="private-test-key internal-error",
            headers={"location": "https://example.com"},
        )

    r = run_decision(
        build_task("credit"), "typesafe/jev-1.14", transport=httpx.MockTransport(handle)
    )
    assert r["status"] == "provider_error" and len(calls) == 1
    assert r["cost"]["usd"] is None
    assert "private-test-key" not in json.dumps(r)


def test_missing_key_and_unapproved_model_make_no_request(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert run_decision(build_task("credit"), "typesafe/jev-1.14")["status"] == "not_configured"
    with pytest.raises(ValueError):
        run_decision(build_task("credit"), "evil/model")


def test_dated_served_version_is_recorded_not_mistaken_for_substitution(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "private-test-key")

    def handle(req):
        return httpx.Response(
            200,
            json={
                "model": "typesafe/jev-1.14-20261014",
                "provider": "TypeSafe",
                "answers": answer_for(build_task("credit")),
                "usage": {"input_tokens": 100, "output_tokens": 0, "cost": 0.0000042},
            },
        )

    r = run_decision(
        build_task("credit"), "typesafe/jev-1.14", transport=httpx.MockTransport(handle)
    )
    assert r["status"] == "complete"
    assert r["returned_model"] == "typesafe/jev-1.14-20261014"


def test_gemini_truncated_answer_keeps_usage_and_reports_invalid_answer():
    from types import SimpleNamespace

    from reconciliation.decision_provider import run_gemini_baseline

    def fake(task, schema):
        return SimpleNamespace(
            text=None,
            model_version="gemini-2.5-flash",
            usage_metadata=SimpleNamespace(
                prompt_token_count=100, candidates_token_count=10, thoughts_token_count=20
            ),
            candidates=[],
        )

    r = run_gemini_baseline(build_task("credit"), model=fake)
    assert r["status"] == "invalid_answer"
    assert r["usage"]["input_tokens"] == 100
    assert r["cost"]["usd"] is not None


def test_priority_consistency_allows_reported_two_decimal_rounding_only():
    t = build_task("credit")
    a = answer_for(t)
    a["priority"]["probabilities"] = {"0": 0.34, "1": 0.65, "2": 0.01, "3": 0.0}
    a["priority"]["score"] = 0.68
    assert validate_answers(t["questions"], a)["priority"]["score"] == 0.68


def test_box_bounds_and_missing_presence_are_separate():
    q = {
        "reference_present": {"type": "noul", "instructions": "Visible?"},
        "reference_location": {"type": "box", "instructions": "The invoice reference"},
    }
    a = {
        "reference_present": {"type": "noul", "noul": 0.1},
        "reference_location": {
            "type": "box",
            "box": {"top": 0.5, "left": 0.4, "bottom": 0.6, "right": 0.7},
            "confidence": 0.9,
        },
    }
    checked = validate_answers(q, a)
    assert checked["reference_present"]["noul"] == 0.1
    assert checked["reference_location"]["box"]["top"] == 0.5
    a["reference_location"]["box"]["right"] = 0.1
    with pytest.raises(ValueError):
        validate_answers(q, a)


def test_request_deadline_is_total_not_reset_by_each_chunk(monkeypatch):
    import asyncio

    import reconciliation.decision_provider as provider

    monkeypatch.setenv("OPENROUTER_API_KEY", "private-test-key")
    monkeypatch.setattr(provider, "REQUEST_SECONDS", 0.01, raising=False)

    async def handle(req):
        await asyncio.sleep(0.1)
        return httpx.Response(200, json={})

    result = provider.run_decision(
        build_task("credit"), "typesafe/jev-1.14", transport=httpx.MockTransport(handle)
    )
    assert result["status"] == "timeout"
    assert result["cost"]["usd"] is None
