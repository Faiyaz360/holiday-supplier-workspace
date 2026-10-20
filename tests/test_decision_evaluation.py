from reconciliation.decision_evaluation import comparison_summary


def test_correct_denominators_failures_and_unknown_cost():
    labels = {"a": {"credit": True, "payment": False}, "b": {"credit": True, "payment": True}}
    records = [
        {
            "case_id": "a",
            "mode": "text",
            "results": {
                "jev": {
                    "status": "complete",
                    "answers": {"credit": True, "payment": True},
                    "elapsed_ms": 100,
                    "cost": {"usd": 0.001, "kind": "reported"},
                }
            },
        },
        {
            "case_id": "b",
            "mode": "text",
            "results": {
                "jev": {
                    "status": "complete",
                    "answers": {"credit": False, "payment": True},
                    "elapsed_ms": 300,
                    "cost": {"usd": 0.002, "kind": "reported"},
                }
            },
        },
        {
            "case_id": "a",
            "mode": "text",
            "results": {
                "jev": {
                    "status": "timeout",
                    "answers": {},
                    "elapsed_ms": 25000,
                    "cost": {"usd": None, "kind": "unknown"},
                }
            },
        },
    ]
    s = comparison_summary(records, labels)["jev"]
    assert s["correct"] == 2 and s["total"] == 4
    assert s["complete_cases"] == 0 and s["failed"] == 1
    assert s["median_ms"] == 200 and s["known_cost_usd"] == 0.003
    assert s["cost_complete"] is False and s["cost_per_1000_successes_usd"] is None
    assert s["claims"]["credit"]["fn"] == 1 and s["claims"]["payment"]["fp"] == 1


def test_visual_and_missing_results_do_not_invent_text_accuracy():
    s = comparison_summary(
        [
            {
                "case_id": "x",
                "mode": "visual",
                "results": {
                    "jev": {
                        "status": "complete",
                        "answers": {},
                        "cost": {"usd": 0, "kind": "reported"},
                    }
                },
            }
        ],
        {},
    )
    assert s == {}
