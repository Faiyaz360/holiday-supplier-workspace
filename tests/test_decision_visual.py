import json

import httpx

from reconciliation.decision_policy import build_task
from reconciliation.decision_provider import run_decision


def test_visual_fixture_only_uses_embedded_png_and_shared_questions(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "private-test-key")
    calls = []

    def handle(req):
        p = json.loads(req.content)
        calls.append(p)
        assert len(p["questions"]) == 3
        assert p["state"][1]["image_url"]["url"].startswith("data:image/png;base64,")
        assert len(p["state"][1]["image_url"]["url"]) < 210000
        return httpx.Response(
            200,
            json={
                "model": p["model"],
                "answers": {k: {"type": "noul", "noul": 0.99} for k in p["questions"]},
                "usage": {"input_tokens": 500, "output_tokens": 0, "cost": 0.000021},
            },
        )

    for model in ["typesafe/jev-1.14", "liquid/d1-ultra"]:
        assert (
            run_decision(build_task("credit_image"), model, transport=httpx.MockTransport(handle))[
                "status"
            ]
            == "complete"
        )
    assert calls[0]["state"] == calls[1]["state"]
