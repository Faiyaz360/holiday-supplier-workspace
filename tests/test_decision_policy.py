import json

import pytest

from reconciliation.decision_policy import (
    build_task,
    case_catalogue,
    proposed_route,
    rules_decisions,
)


def test_common_questions_and_labels_never_enter_task():
    task = build_task("credit")
    assert list(task["questions"]) == [
        "credit",
        "payment",
        "currency",
        "duplicate",
        "documents",
        "clarification",
        "next_step",
        "priority",
    ]
    assert [q["type"] for q in task["questions"].values()] == ["noul"] * 6 + ["choice", "score"]
    assert "expected" not in json.dumps(task)
    assert "label_status" not in json.dumps(task)
    assert len(case_catalogue()) >= 6


def test_multi_claim_and_negation_rules_are_transparent():
    assert rules_decisions(build_task("two_claims"))["answers"]["credit"] is True
    assert rules_decisions(build_task("two_claims"))["answers"]["currency"] is True
    assert rules_decisions(build_task("negated"))["answers"]["credit"] is False
    assert all(
        not isinstance(v, dict) for v in rules_decisions(build_task("credit"))["answers"].values()
    )


def test_missing_reference_overrides_confident_model_without_rewriting_it():
    answers = {"next_step": {"choice": "investigate_evidence"}, "credit": {"noul": 0.99}}
    result = proposed_route(build_task("uncertain"), answers, 0.8)
    assert result["route"] == "clarify_reference"
    assert result["model_route"] == "investigate_evidence"
    assert answers["next_step"]["choice"] == "investigate_evidence"


@pytest.mark.parametrize("case", ["../.env", "", "not_a_case"])
def test_unknown_case_rejected(case):
    with pytest.raises(ValueError):
        build_task(case)


def test_visual_box_is_not_a_shared_question():
    task = build_task("credit_image")
    assert task["mode"] == "visual"
    assert all(q["type"] != "box" for q in task["questions"].values())
