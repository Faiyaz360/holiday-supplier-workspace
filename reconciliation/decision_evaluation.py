"""Measured comparisons; draft-label agreement is not production accuracy."""

import statistics

from .decision_policy import FLAGS


def categorical(answers):
    output = {}
    for key, item in answers.items():
        if not isinstance(item, dict):
            output[key] = item
        elif item.get("type") == "noul":
            output[key] = item["noul"] >= 0.5
        elif item.get("type") == "choice":
            output[key] = item["choice"]
        elif item.get("type") == "score":
            output[key] = max(item["probabilities"], key=item["probabilities"].get)
    if isinstance(output.get("priority"), str):
        output["priority"] = int(output["priority"])
    return output


def comparison_summary(records, labels):
    results = {}
    for record in records:
        if record.get("mode", record.get("task", {}).get("mode", "text")) != "text":
            continue
        expected = labels.get(record["case_id"], {})
        for name, result in record["results"].items():
            s = results.setdefault(
                name,
                {
                    "attempts": 0,
                    "completed": 0,
                    "failed": 0,
                    "correct": 0,
                    "total": 0,
                    "complete_cases": 0,
                    "scored_cases": 0,
                    "latencies_ms": [],
                    "median_ms": None,
                    "known_cost_usd": 0,
                    "unknown_costs": 0,
                    "estimated_costs": 0,
                    "cost_complete": True,
                    "cost_per_1000_successes_usd": None,
                    "claims": {k: {"tp": 0, "fp": 0, "fn": 0} for k in FLAGS},
                },
            )
            s["attempts"] += 1
            cost = result.get("cost", {})
            usd = cost.get("usd")
            if usd is None:
                s["unknown_costs"] += 1
                s["cost_complete"] = False
            else:
                s["known_cost_usd"] += usd
            if cost.get("kind") == "estimated":
                s["estimated_costs"] += 1
            if result["status"] != "complete":
                s["failed"] += 1
                continue
            s["completed"] += 1
            if result.get("elapsed_ms") is not None:
                s["latencies_ms"].append(result["elapsed_ms"])
            got = categorical(result["answers"])
            if expected:
                correct = sum(
                    type(got.get(k)) is type(v) and got.get(k) == v for k, v in expected.items()
                )
                s["correct"] += correct
                s["total"] += len(expected)
                s["scored_cases"] += 1
                s["complete_cases"] += int(correct == len(expected))
                for k in FLAGS:
                    if k not in expected:
                        continue
                    for field, condition in [
                        ("tp", expected[k] is True and got.get(k) is True),
                        ("fp", expected[k] is False and got.get(k) is True),
                        ("fn", expected[k] is True and got.get(k) is not True),
                    ]:
                        s["claims"][k][field] += int(condition)
    for s in results.values():
        if s["latencies_ms"]:
            s["median_ms"] = statistics.median(s["latencies_ms"])
        if s["cost_complete"] and s["completed"]:
            s["cost_per_1000_successes_usd"] = s["known_cost_usd"] * 1000 / s["completed"]
    return results
