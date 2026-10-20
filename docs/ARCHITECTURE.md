# Architecture and module map

A finance operator needs traceable records, a useful explanation and a clear next action. The service keeps source data, model suggestions and human decisions separate.

## Layers

| Area | Main modules | Responsibility |
| --- | --- | --- |
| Reconciliation | `core.py`, `statement_import.py`, `fixtures.py` | Validate CSVs, preserve decimal amounts and match supplier invoices |
| Saved evidence | `runs.py`, `source_evidence.py`, `cloud_storage.py` | Keep source bundles and revisions consistent |
| HTTP application | `hosted_api.py`, `api.py`, `hosted_workspace.py` | Authorise requests and serve the workspace |
| Agent execution | `agent_runner.py`, `tools.py`, `model_allowance.py` | Run bounded ADK investigations and validate their proposals |
| Questions | `question_agent.py`, `conversation_store.py` | Save statement questions, selected evidence and outcomes |
| Hotel replies | `reply_agent.py`, `reply_store.py` | Keep original correspondence and proposals separate from confirmed actions |
| Follow-up | `case_store.py`, `hosted_workflow.py` | Record human decisions without rewriting financial evidence |
| Model comparison | `decision_provider.py`, `decision_policy.py`, `decision_workflow.py`, `decision_evaluation.py`, `decision_store.py` | Evaluate the same fictional tasks, preserve failures and account for model usage |
| Presentation | `report.py`, `assets/` | Render factual reports, readable emails, UI state and the guided tour |

## One statement through the system

1. The server validates a statement preview without saving a run or calling a model.
2. Confirmation stores the source statement and ledger snapshot with a reconciliation result.
3. The browser opens invoice findings from the saved evidence.
4. An explicit question or investigation invokes an ADK runner with supplier-scoped tools, a request budget and a deadline.
5. The server checks the selected evidence and supplies financial wording from the saved source records.
6. A person reviews the draft or confirms a follow-up. That action becomes a separate saved record.

The supplier email is a readable projection of saved exceptions. The original query remains available for inspection. Copying the email or opening a citation does not call a model or send a message.

## Durable records and concurrency

The Google-backed adapter stores run bundles in Cloud Storage. Conditional writes use object generations to prevent one client overwriting another client's newer work. Request identifiers let the service reuse completed outcomes instead of repeating work. Uncertain paid attempts remain uncertain rather than being silently restarted.

A resolved follow-up records the person's decision. It does not change the original finding, prove payment or make two differing sources equal. Removing an invoice from a later statement also does not prove settlement.

## Access and tools

The service fixes supplier scope at configuration time. Read, execute and review passes are distinct. A password session exposes the permitted workspace without sending those service passes to the browser. Expiring sessions and same-origin checks protect browser actions. The optional verified identity path is separate from the shared-password portfolio setup.

The model has no payment, email-send or human-review tool. Financial explanations use validated evidence identifiers. A supplier's claimed credit is a claim to check against finance records, not proof that a credit exists.

## Reading order

Start with `core.py`, then `hosted_workflow.py` and `hosted_api.py`. Follow an individual feature into its agent and store modules. The JavaScript files in `assets/` manage browser state; `onboarding.js` advances on completed workflow actions rather than a sequence of Next buttons.
