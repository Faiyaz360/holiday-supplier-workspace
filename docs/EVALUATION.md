# Evaluation and what the results establish

The project checks financial rules, workflow behaviour and model behaviour separately. A fluent answer and a correct tool call are different claims.

## Automated checks

Python tests exercise CSV validation, exact monetary comparisons, supplier scope, request budgets, source preservation, conditional writes, authentication, human confirmation and recovery. Offline model fixtures drive the real ADK runner. Node browser-state tests cover navigation, stale responses, paid-call controls, evidence messages and guided-tour transitions.

The local checks use test-only credentials and fictional records. They do not certify Google Cloud permissions, provider availability or a production finance integration.

## ADK evaluation set

`evals/` contains a twelve-scenario evaluation set and its scoring configuration. `check-evals` validates the schemas without model use. Running `adk eval` is a separate paid operation:

```sh
uv run adk eval reconciler evals/reconciliation.evalset.json --config_file_path evals/test_config.json
```

The bundled historical snapshot passed twelve tool-use checks and one of twelve wording-similarity checks. The eleven wording failures remain visible under the original criteria. Wording overlap is not a financial correctness score. A separate factual audit checks saved source coverage and permitted tool use.

## Specialist decision models

The workspace compares Jev, d1-ultra, Gemini and transparent rules using fictional hotel messages. Text tasks include claim detection, next-step routing and advisory review-priority scoring. Visual credit-note experiments are separate from the text cohort.

The headline comparison uses the same completed text cases across the three providers. It keeps response time, model cost and agreement with author-written draft answers on separate labelled scales. Unknown cost stays unknown. Failed attempts and missed claims remain inspectable.

Median response time includes observed request overhead. Provider-reported and estimated costs have different confidence. Projected costs per 1,000 equivalent completed cases are an illustration at the recorded rates, not a quote or billing guarantee. Probability is a model estimate, not measured accuracy. The small case set and draft labels do not support a production model-selection claim.

Recorded replay makes no provider call. Changing a display threshold on saved output makes no provider call. Fresh comparisons and the optional `tools/run_decision_trial.py` runner require credentials and explicit live-usage approval and can incur charges. The bundled trial budget seed preserves the recorded experiment's usage; it is not an unlimited allowance for another deployment.

## Evidence included here

The repository includes fictional cases, recorded comparison results, the historical evaluation snapshot and source-level tests. Private cloud readbacks, account identifiers, deployment logs and run bundles from the owner's workspace are deliberately excluded.
