"""Retry timing at the provider boundary, with no network or real waiting."""

import asyncio
import json

import pytest
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import errors

from reconciliation.adk_agent import ACTIVE_METER, BudgetedModel, Meter


class ThrottledModel(BaseLlm):
    model: str = "offline-retry-test"
    calls: int = 0
    failures: int = 2
    status: int = 429
    hint: object = "2.5s"

    async def generate_content_async(self, llm_request, stream=False):
        self.calls += 1
        if self.calls <= self.failures:
            raise errors.APIError(
                self.status,
                {
                    "error": {
                        "message": "private-provider-detail",
                        "details": [
                            {
                                "@type": "type.googleapis.com/google.rpc.RetryInfo",
                                "retryDelay": self.hint,
                            }
                        ],
                    }
                },
            )
        yield LlmResponse()


def invoke(monkeypatch, model, timeout=120):
    waits = []

    async def sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr("reconciliation.adk_agent.asyncio.sleep", sleep)
    meter = Meter(timeout=timeout)

    async def run():
        token = ACTIVE_METER.set(meter)
        try:
            wrapper = BudgetedModel(model=model.model, inner=model, require_live_approval=False)
            return [item async for item in wrapper.generate_content_async(None)]
        finally:
            ACTIVE_METER.reset(token)

    return run, waits, meter


def test_rate_limit_honours_retry_info_and_increases_wait(monkeypatch):
    model = ThrottledModel()
    run, waits, meter = invoke(monkeypatch, model)
    assert len(asyncio.run(run())) == 1
    assert waits == [2.5, 5.0]
    assert meter.calls == model.calls == 3
    assert "private-provider-detail" not in json.dumps(meter.events)


@pytest.mark.parametrize("hint", [None, "NaNs", "-1s", "1e10s", [], "9" * 100 + "s"])
def test_rate_limit_without_usable_hint_stops_without_retry(monkeypatch, hint):
    model = ThrottledModel(hint=hint)
    run, waits, meter = invoke(monkeypatch, model)
    with pytest.raises(RuntimeError, match="Model provider request failed"):
        asyncio.run(run())
    assert model.calls == meter.calls == 1
    assert waits == []
    assert "private-provider-detail" not in json.dumps(meter.events)


def test_retry_delay_outside_deadline_stops_without_waiting(monkeypatch):
    model = ThrottledModel(hint="120s")
    run, waits, meter = invoke(monkeypatch, model, timeout=30)
    with pytest.raises(RuntimeError, match="Model provider request failed"):
        asyncio.run(run())
    assert model.calls == meter.calls == 1
    assert waits == []


def test_non_retryable_error_does_not_wait_even_with_retry_info(monkeypatch):
    model = ThrottledModel(status=403)
    run, waits, meter = invoke(monkeypatch, model)
    with pytest.raises(RuntimeError, match="Model provider request failed"):
        asyncio.run(run())
    assert model.calls == meter.calls == 1
    assert waits == []


def test_server_error_without_hint_uses_bounded_backoff(monkeypatch):
    model = ThrottledModel(status=503, hint=None)
    run, waits, meter = invoke(monkeypatch, model)
    assert len(asyncio.run(run())) == 1
    assert waits == [1.0, 2.0]
    assert meter.calls == 3


def test_continued_throttling_stops_after_three_attempts(monkeypatch):
    model = ThrottledModel(failures=99)
    run, waits, meter = invoke(monkeypatch, model)
    with pytest.raises(RuntimeError, match="Model provider request failed"):
        asyncio.run(run())
    assert model.calls == meter.calls == 3
    assert waits == [2.5, 5.0]


def test_cancellation_during_provider_wait_does_not_retry(monkeypatch):
    model = ThrottledModel()
    run, waits, meter = invoke(monkeypatch, model)

    async def cancel(seconds):
        raise asyncio.CancelledError()

    monkeypatch.setattr("reconciliation.adk_agent.asyncio.sleep", cancel)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run())
    assert model.calls == meter.calls == 1


def test_partial_response_is_never_replayed_after_throttling(monkeypatch):
    class PartialModel(ThrottledModel):
        async def generate_content_async(self, llm_request, stream=False):
            self.calls += 1
            yield LlmResponse()
            raise errors.APIError(
                429,
                {
                    "error": {
                        "details": [
                            {
                                "@type": "type.googleapis.com/google.rpc.RetryInfo",
                                "retryDelay": "2s",
                            }
                        ]
                    }
                },
            )

    model = PartialModel()
    run, waits, meter = invoke(monkeypatch, model)
    with pytest.raises(RuntimeError, match="Model provider request failed"):
        asyncio.run(run())
    assert model.calls == meter.calls == 1
    assert waits == []
