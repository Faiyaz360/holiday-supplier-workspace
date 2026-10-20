"""Fixed, bounded decision endpoints. Errors never expose provider bodies or credentials."""

import asyncio
import base64
import copy
import hashlib
import json
import math
import os
import re
import time
from pathlib import Path

import httpx

MODELS = ("typesafe/jev-1.14", "liquid/d1-ultra")
PRICE_DATE = "2026-10-20"
REQUEST_SECONDS = 25


def number(value, low=0, high=float("inf")):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError("Invalid numeric provider value")
    return value


def validate_answers(questions, answers):
    if not isinstance(answers, dict) or set(answers) != set(questions):
        raise ValueError("Missing or unexpected decisions")
    checked = {}
    for key, question in questions.items():
        item = answers[key]
        if not isinstance(item, dict) or item.get("type") != question["type"]:
            raise ValueError("Wrong decision type")
        kind = question["type"]
        if kind == "noul":
            checked[key] = {"type": kind, "noul": number(item.get("noul"), 0, 1)}
            continue
        if kind == "box":
            box = item.get("box")
            if not isinstance(box, dict) or set(box) != {"top", "left", "bottom", "right"}:
                raise ValueError("Invalid image region")
            box = {k: number(v, 0, 1) for k, v in box.items()}
            if box["top"] >= box["bottom"] or box["left"] >= box["right"]:
                raise ValueError("Reversed image region")
            checked[key] = {
                "type": "box",
                "box": box,
                "confidence": number(item.get("confidence"), 0, 1),
            }
            continue
        options = (
            list(question["criteria"])
            if kind == "choice"
            else [str(i) for i in range(len(question["criteria"]))]
        )
        probabilities = item.get("probabilities")
        if not isinstance(probabilities, dict) or set(probabilities) != set(options):
            raise ValueError("Wrong probability choices")
        probs = {k: number(v, 0, 1) for k, v in probabilities.items()}
        if abs(sum(probs.values()) - 1) > 0.001:
            raise ValueError("Invalid probability distribution")
        checked[key] = {"type": kind, "probabilities": probs}
        if "confidence" in item:
            checked[key]["confidence"] = number(item["confidence"], 0, 1)
        if kind == "choice":
            choice = item.get("choice")
            if choice not in options or probs[choice] + 0.001 < max(probs.values()):
                raise ValueError("Invalid winning choice")
            checked[key]["choice"] = choice
        elif kind == "score":
            score = number(item.get("score"), 0, len(options) - 1)
            derived = sum(int(k) * v for k, v in probs.items())
            # Some served models round every probability and score to two decimals.
            # The maximum four-level rounding error is 0.005*(1+2+3)+0.005.
            rounded = all(round(v, 2) == v for v in probs.values()) and round(score, 2) == score
            tolerance = 0.035000001 if rounded else 0.000001
            if abs(score - derived) > tolerance:
                raise ValueError("Inconsistent priority score")
            legend = {str(i): v for i, v in enumerate(question["criteria"])}
            if "legend" in item and item["legend"] != legend:
                raise ValueError("Unexpected priority legend")
            checked[key].update(score=score, legend=legend, probability_weighted_score=derived)
        else:
            raise ValueError("Unsupported decision primitive")
    return checked


def _base(model):
    return {
        "schema_version": 1,
        "status": "provider_error",
        "requested_model": model,
        "returned_model": None,
        "provider": "OpenRouter" if model in MODELS else "Google",
        "answers": {},
        "elapsed_ms": 0,
        "usage": {"input_tokens": None, "output_tokens": None},
        "cost": {"usd": None, "kind": "unknown", "source": "Provider charge not confirmed"},
        "http_status": None,
    }


def _usage(data):
    if not isinstance(data, dict):
        raise ValueError("Invalid usage")
    usage = {}
    for key in ("input_tokens", "output_tokens"):
        value = data.get(key)
        if value is not None and (type(value) is not int or not 0 <= value <= 10_000_000):
            raise ValueError("Invalid usage count")
        usage[key] = value
    if data.get("cost") is not None:
        cost = {
            "usd": number(data["cost"], 0, 10),
            "kind": "reported",
            "source": "OpenRouter usage.cost; excludes credit-purchase fees and tax",
        }
    elif usage["input_tokens"] is not None:
        cost = {
            "usd": usage["input_tokens"] * 0.042 / 1_000_000,
            "kind": "estimated",
            "source": "OpenRouter model input list rate USD 0.042/M; checked " + PRICE_DATE,
        }
    else:
        cost = {"usd": None, "kind": "unknown", "source": "Provider usage not reported"}
    return usage, cost


async def _post_decision(payload, key, transport):
    async with asyncio.timeout(REQUEST_SECONDS):
        async with httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(REQUEST_SECONDS, connect=min(5, REQUEST_SECONDS)),
            follow_redirects=False,
        ) as client:
            async with client.stream(
                "POST",
                "https://openrouter.ai/api/alpha/decisions",
                json=payload,
                headers={"Authorization": "Bearer " + key},
            ) as response:
                if response.status_code != 200:
                    return response.status_code, b""
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 256 * 1024:
                        raise ValueError("Provider response exceeded its limit")
                return response.status_code, bytes(raw)


def run_decision(task, model_id, *, transport=None):
    if model_id not in MODELS:
        raise ValueError("Model is outside the approved comparison")
    result = _base(model_id)
    if model_id == "liquid/d1-ultra" and any(
        q["type"] == "box" for q in task["questions"].values()
    ):
        result.update(status="not_supported")
        result["cost"] = {
            "usd": 0,
            "kind": "not_called",
            "source": "Box is a Jev-only primitive; no d1-ultra request sent",
        }
        return result
    key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not key:
        result["status"] = "not_configured"
        result["cost"] = {"usd": 0, "kind": "not_called", "source": "No request sent"}
        return result
    payload = {
        "model": model_id,
        "state": copy.deepcopy(task["state"]),
        "questions": copy.deepcopy(task["questions"]),
    }
    if (
        not 1 <= len(payload["questions"]) <= 8
        or len(json.dumps(payload, ensure_ascii=False).encode()) > 32000
    ):
        raise ValueError("Task exceeds the comparison limits")
    if task.get("mode") == "visual":
        image = (Path(__file__).parent / "assets/decision-fixtures/credit-note.png").read_bytes()
        if len(image) > 150 * 1024 or not image.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("Invalid bundled document")
        manifest = json.loads(
            (Path(__file__).parent / "assets/decision-fixtures/credit-note.json").read_text(
                encoding="utf-8"
            )
        )
        if (
            hashlib.sha256(image).hexdigest() != manifest["sha256"]
            or manifest.get("fictional") is not True
        ):
            raise ValueError("Bundled document provenance changed")
        payload["state"] = [
            json.dumps(task["state"], ensure_ascii=False),
            {
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64," + base64.b64encode(image).decode()},
            },
        ]
    started = time.perf_counter()
    try:
        status, raw = asyncio.run(_post_decision(payload, key, transport))
        result["http_status"] = status
        if status != 200:
            return result
        data = json.loads(raw)
        result["validation_stage"] = "usage"
        result["usage"], result["cost"] = _usage(data.get("usage", {}))
        result["validation_stage"] = "served_model"
        actual = data.get("model")
        aliases = [model_id, model_id.split("/", 1)[1]]
        pattern = "(?:" + "|".join(re.escape(a) for a in aliases) + r")(?:\.0)?(?:-\d{8})?"
        if not isinstance(actual, str) or re.fullmatch(pattern, actual) is None:
            raise ValueError("Unexpected served model")
        result["returned_model"] = actual
        result["validation_stage"] = "answers"
        try:
            result["answers"] = validate_answers(task["questions"], data.get("answers"))
        except ValueError as error:
            result["validation_error"] = str(error)
            if isinstance(data.get("answers"), dict):
                diagnostic = {}
                for name, question in task["questions"].items():
                    raw_answer = data["answers"].get(name, {})
                    if not isinstance(raw_answer, dict):
                        continue
                    values = raw_answer.get("probabilities", {})
                    if isinstance(values, dict):
                        finite = [
                            v
                            for v in values.values()
                            if type(v) in (int, float) and math.isfinite(v)
                        ]
                        diagnostic[name] = {
                            "probability_sum": sum(finite),
                            "option_count": len(values),
                        }
                    if type(raw_answer.get("score")) in (int, float) and math.isfinite(
                        raw_answer["score"]
                    ):
                        diagnostic.setdefault(name, {})["score"] = raw_answer["score"]
                result["validation_details"] = diagnostic
            raise
        result["status"] = "complete"
        result.pop("validation_stage", None)
    except (TimeoutError, httpx.TimeoutException):
        result["status"] = "timeout"
    except (ValueError, TypeError, KeyError, OverflowError):
        result["status"] = "invalid_answer"
    except httpx.HTTPError:
        result["status"] = "provider_error"
    finally:
        if result["status"] == "complete":
            result.pop("validation_stage", None)
        result["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
    return result


def run_gemini_baseline(task, *, model=None):
    model_id = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
    result = _base(model_id)
    if task.get("mode") == "visual":
        result["status"] = "not_supported"
        result["cost"] = {"usd": 0, "kind": "not_called", "source": "Text baseline only"}
        return result
    if model is None and (
        os.getenv("DEMO_LIVE_APPROVED") != "1"
        or not (os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"))
    ):
        result["status"] = "not_configured"
        result["cost"] = {"usd": 0, "kind": "not_called", "source": "No request sent"}
        return result
    schema = {
        "type": "object",
        "properties": {
            key: {"type": "boolean"}
            if q["type"] == "noul"
            else {"type": "string", "enum": list(q["criteria"])}
            if q["type"] == "choice"
            else {"type": "integer", "minimum": 0, "maximum": 3}
            for key, q in task["questions"].items()
        },
        "required": list(task["questions"]),
        "additionalProperties": False,
    }
    started = time.perf_counter()
    try:
        if model is not None:
            response = model(task, schema)
        else:
            from google import genai
            from google.genai import types

            with genai.Client(
                api_key=os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"),
                http_options=types.HttpOptions(
                    timeout=25000, retry_options=types.HttpRetryOptions(attempts=1)
                ),
            ) as client:
                response = client.models.generate_content(
                    model=model_id,
                    contents=json.dumps(
                        {"state": task["state"], "questions": task["questions"]}, ensure_ascii=False
                    ),
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_json_schema=schema,
                        max_output_tokens=2048,
                        temperature=0,
                    ),
                )
        result["returned_model"] = getattr(response, "model_version", None)
        result["validation_stage"] = "usage"
        meta = response.usage_metadata
        inp, out = (
            getattr(meta, "prompt_token_count", None),
            getattr(meta, "candidates_token_count", None),
        )
        thoughts = getattr(meta, "thoughts_token_count", None) or 0
        result["usage"] = {
            "input_tokens": inp,
            "output_tokens": None if out is None else out + thoughts,
        }
        result["cost"] = {
            "usd": None,
            "kind": "unknown",
            "source": "Google model rate not yet verified for this served version",
        }
        rates = {
            "gemini-2.5-flash": (0.30, 2.50),
            "gemini-2.5-flash-lite": (0.10, 0.40),
            "gemini-3.1-flash-lite": (0.25, 1.50),
            "gemini-3.1-flash-lite-preview": (0.25, 1.50),
            "gemini-3.8-flash": (0.75, 3.75),
        }
        served = getattr(response, "model_version", None) or model_id
        if served in rates and type(inp) is int and type(out) is int:
            a, b = rates[served]
            result["cost"] = {
                "usd": (inp * a + (out + thoughts) * b) / 1_000_000,
                "kind": "estimated",
                "source": "Google standard paid token rates, checked "
                + PRICE_DATE
                + "; includes reported thinking tokens",
            }
        result["validation_stage"] = "answer"
        text = response.text
        if not isinstance(text, str) or len(text.encode()) > 16000:
            raise ValueError("Invalid baseline response")
        answer = json.loads(text)
        if set(answer) != set(task["questions"]):
            raise ValueError("Missing baseline decisions")
        for key, q in task["questions"].items():
            value = answer[key]
            if q["type"] == "noul" and type(value) is not bool:
                raise ValueError("Invalid claim flag")
            if q["type"] == "choice" and value not in q["criteria"]:
                raise ValueError("Invalid route")
            if q["type"] == "score" and (type(value) is not int or not 0 <= value <= 3):
                raise ValueError("Invalid priority")
        result.update(
            status="complete",
            answers=answer,
            returned_model=getattr(response, "model_version", None),
        )
    except (ValueError, TypeError, KeyError):
        result["status"] = "invalid_answer"
        result["answers"] = {}
    except Exception as error:
        result["status"] = "provider_error"
        result["answers"] = {}
        result["error_type"] = (
            type(error).__name__
            if type(error).__name__
            in {"ClientError", "ServerError", "TimeoutError", "ReadTimeout", "ConnectError"}
            else "ProviderError"
        )
        code = getattr(error, "code", None)
        if type(code) is int and 100 <= code <= 599:
            result["http_status"] = code
    finally:
        if result["status"] == "complete":
            result.pop("validation_stage", None)
        result["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
    return result
